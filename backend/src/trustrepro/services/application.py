"""Conversation, one approval, one persistent Run; no prescribed scientific workflow."""

import copy
import hashlib
import json

from sqlalchemy import func, select

from trustrepro.domain.errors import BusinessError
from trustrepro.domain.runtime import Allocation, Proposal, bind_values
from trustrepro.infrastructure.db.models import (
    Account,
    AgentSession,
    Artifact,
    Card,
    Conversation,
    Event,
    Grant,
    Job,
    Message,
    Plan,
    Project,
    Report,
    Run,
    RuntimeEvent,
    Source,
    new_id,
    now_ms,
)
from trustrepro.services.jobs import error_value, status_value
from trustrepro.services.runtime import budget, require_budget


def record(db, model, identifier):
    row = db.get(model, identifier)
    if row is None:
        raise BusinessError("NOT_FOUND", "记录不存在")
    return row


def event(db, conversation_id, kind="refresh", payload=None):
    db.add(Event(conversation_id=conversation_id, kind=kind, payload=payload or {}))


def project_for(db, conversation_id):
    return db.scalar(select(Project).where(Project.conversation_id == conversation_id))


def session_for(db, chat, kind="research", run=None, generation=1):
    from trustrepro.services.preferences import model_role

    key = f"{kind}:{run.id if run else chat.id}:{generation}"
    row = db.scalar(select(AgentSession).where(AgentSession.scope_key == key))
    if row is None:
        row = AgentSession(
            id=new_id("agent"),
            scope_key=key,
            conversation_id=chat.id,
            account_id=chat.account_id,
            kind=kind,
            model_binding=copy.deepcopy(chat.model_bindings.get(model_role(kind), {})),
            run_id=run.id if run else None,
            state={},
        )
        db.add(row)
        db.flush()
    return row


def create_conversation(db, client_key, settings, models=None):
    row = db.scalar(select(Conversation).where(Conversation.client_key == client_key))
    if row:
        return row
    account = Account(id=new_id("budget"), policy={})
    db.add(account)
    db.flush()
    from trustrepro.services.preferences import default_bindings

    row = Conversation(
        id=new_id("chat"),
        account_id=account.id,
        client_key=client_key,
        model_bindings=default_bindings(db, models),
    )
    db.add(row)
    db.flush()
    return row


def append_message(db, conversation_id, role, content, identifier=None, blocks=None):
    row = Message(
        id=identifier or new_id("message"),
        conversation_id=conversation_id,
        role=role,
        content=content,
        blocks=blocks or [],
    )
    db.add(row)
    db.flush()
    event(db, conversation_id, "message", {"id": row.id})
    return row


def submit_message(db, conversation_id, client_key, content):
    chat = db.get(Conversation, conversation_id, with_for_update=True)
    if not chat:
        raise BusinessError("NOT_FOUND", "会话不存在")
    identifier = "user-" + hashlib.sha256(f"{chat.id}:{client_key}".encode()).hexdigest()
    old = db.get(Message, identifier)
    if old:
        if old.content != content:
            raise BusinessError("IDEMPOTENCY_CONFLICT", "消息标识已用于其他内容")
        return
    if not content.strip():
        raise BusinessError("EMPTY_MESSAGE", "请输入消息")
    append_message(db, chat.id, "user", content, identifier)
    from trustrepro.services import lifecycle

    row = db.scalar(
        select(AgentSession)
        .join(Run, AgentSession.run_id == Run.id)
        .where(
            AgentSession.conversation_id == chat.id,
            AgentSession.kind == "execution",
            Run.status.in_(["RUNNING", "PAUSED", "PAUSING"]),
        )
        .order_by(AgentSession.created_at.desc())
    )
    row = row or session_for(db, chat)
    chat.input_version += 1
    project = project_for(db, chat.id)
    if project:
        for card in db.scalars(
            select(Card).where(Card.project_id == project.id, Card.status.in_(["ready", "pending"]))
        ):
            card.status = "revising"
    lifecycle.delivery(
        db,
        row,
        identifier,
        {"messageId": identifier, "content": content, "inputVersion": chat.input_version},
    )
    if row.desired_state == "PAUSED" or (
        row.kind == "research" and row.desired_state == "BUDGET_LIMITED"
    ):
        lifecycle.request_resume(db, row, "resume-input:" + identifier)
    if chat.title == "新对话":
        chat.title = content[:80]
    event(db, chat.id)


def create_project(db, chat, title, goal, source=""):
    row = project_for(db, chat.id)
    if row:
        return {"id": row.id, "title": row.title, "existing": True}
    row = Project(
        id=new_id("project"), conversation_id=chat.id, title=title, goal=goal, source=source
    )
    db.add(row)
    chat.title = title
    event(db, chat.id)
    return {"id": row.id, "title": row.title}


def propose(db, chat, content, input_version=None, pending=False):
    chat = db.get(Conversation, chat.id, with_for_update=True)
    if input_version is not None and input_version != chat.input_version:
        raise BusinessError("STALE_INPUT", "有新的用户输入，请读取并处理后重新提交方案")
    project = project_for(db, chat.id)
    if project is None:
        raise BusinessError("PROJECT_REQUIRED", "请先创建当前复现项目")
    value = Proposal.model_validate(content).model_dump()
    value = bind_values(value, {})
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError):
        raise BusinessError("INVALID_CARD", "参数必须是有效 JSON，不能包含非有限数") from None
    available = set(db.scalars(select(Source.id).where(Source.conversation_id == chat.id)))
    if set(value["sources"]) - available:
        raise BusinessError("INVALID_SOURCE", "来源不属于当前会话")
    for old in db.scalars(
        select(Card).where(
            Card.project_id == project.id, Card.status.in_(["ready", "pending", "revising"])
        )
    ):
        old.status = "replaced"
    version = (
        db.scalar(select(func.max(Card.version)).where(Card.project_id == project.id)) or 0
    ) + 1
    row = Card(
        id=new_id("card"),
        project_id=project.id,
        version=version,
        content=value,
        input_version=chat.input_version,
        status="pending" if pending else "ready",
    )
    db.add(row)
    db.flush()
    event(db, chat.id)
    return {"cardId": row.id, "version": version, "revision": row.revision, "status": row.status}


def edit_card(db, chat, card_id, version, values, allocation=None, revision=1, client_key=None):
    db.get(Conversation, chat.id, with_for_update=True)
    project = project_for(db, chat.id)
    row = db.get(Card, card_id, with_for_update=True)
    if row is None:
        raise BusinessError("NOT_FOUND", "任务卡不存在")
    if project is None or row.project_id != project.id:
        raise BusinessError("NOT_FOUND", "任务卡不存在")
    receipt = {"key": client_key, "revision": revision, "values": values, "allocation": allocation}
    if client_key and row.edit_receipt.get("key") == client_key:
        if row.edit_receipt != receipt:
            raise BusinessError("IDEMPOTENCY_CONFLICT", "保存标识已用于其他修改")
        return
    if row.version != version or row.revision != revision or row.status != "ready":
        raise BusinessError("VERSION_CONFLICT", "任务卡已变更或已批准")
    content = bind_values(row.content, values)
    if allocation is not None:
        content["allocation"] = Allocation.model_validate(allocation).model_dump()
    row.content, row.edit_receipt = content, receipt
    row.revision += 1
    event(db, chat.id)


def confirm(db, chat, card_id, version, revision=1):
    # The account row serializes approval, model reservations and subsequent grants.
    db.get(Conversation, chat.id, with_for_update=True)
    account = db.get(Account, chat.account_id, with_for_update=True)
    card = record(db, Card, card_id)
    project = project_for(db, chat.id)
    if project is None or card.project_id != project.id or card.version != version:
        raise BusinessError("VERSION_CONFLICT", "任务卡版本不一致")
    old = db.scalar(select(Plan).where(Plan.card_id == card.id))
    if old:
        return db.scalar(select(Run).where(Run.plan_id == old.id))
    if card.status != "ready" or card.revision != revision:
        raise BusinessError("VERSION_CONFLICT", "请确认最新任务卡")
    allocation = copy.deepcopy(card.content["allocation"])
    if account.confirmed:
        allocation["maxCost"] = max(allocation["maxCost"], account.policy["maxCost"])
        allocation["maxHours"] = max(allocation["maxHours"], account.policy["maxHours"])
    account.policy = allocation
    account.confirmed = True
    require_budget(db, account.id)
    plan = Plan(
        id=new_id("plan"),
        card_id=card.id,
        project_id=project.id,
        content=copy.deepcopy(card.content),
    )
    db.add(plan)
    db.flush()
    run = Run(id=new_id("run"), plan_id=plan.id, account_id=chat.account_id, project_id=project.id)
    db.add(run)
    db.flush()
    card.status = "approved"
    session_for(db, chat, "execution", run)
    event(db, chat.id)
    return run


def artifact_value(row):
    return {
        "id": row.id,
        "runId": row.run_id,
        "projectId": row.project_id,
        "name": row.name,
        "size": row.size,
        "sha256": row.sha256,
        "mediaType": row.media_type,
        "sourcePath": row.source_path,
    }


def job_value(row):
    arguments = row.arguments
    result = row.result
    if row.kind == "write_file":
        arguments = {"path": arguments["path"], "bytes": len(arguments["content"].encode())}
    elif row.kind == "apply_patch":
        arguments = {"changes": [{"path": c["path"]} for c in arguments["changes"]]}
    if row.kind == "export" and isinstance(result, dict):
        # The full accounting snapshot belongs to the downloadable publication.
        result = {k: v for k, v in result.items() if k != "usage"}
    return {
        "id": row.id,
        "runId": row.run_id,
        "kind": row.kind,
        "status": status_value(row),
        "arguments": arguments,
        "progress": row.progress,
        "result": result,
        "error": error_value(row),
        "createdAt": row.created_at,
        "startedAt": row.started_at,
        "finishedAt": row.finished_at,
    }


def report_value(db, row):
    export_job = db.get(Job, row.export_job_id) if row.export_job_id else None
    artifacts = [artifact_value(record(db, Artifact, i)) for i in row.artifact_ids]
    return {
        "id": row.id,
        "runId": row.run_id,
        "projectId": row.project_id,
        "version": row.version,
        "result": row.result,
        "artifacts": artifacts,
        "exportStatus": row.export_status,
        "exportJob": {
            "id": export_job.id,
            "startedAt": export_job.started_at,
            "finishedAt": export_job.finished_at,
        }
        if export_job
        else None,
        "error": row.error,
        "createdAt": row.created_at,
    }


def run_value(db, row):
    from trustrepro.services.lifecycle import automation

    agent = db.scalar(
        select(AgentSession)
        .where(AgentSession.run_id == row.id, AgentSession.kind == "execution")
        .order_by(AgentSession.created_at.desc())
    )
    return {
        "id": row.id,
        "projectId": row.project_id,
        "planId": row.plan_id,
        "status": row.status,
        "automation": automation(db, agent),
        "error": row.error,
        "createdAt": row.created_at,
        "finishedAt": row.finished_at,
        "jobs": [
            job_value(j)
            for j in db.scalars(select(Job).where(Job.run_id == row.id).order_by(Job.created_at))
        ],
    }


def snapshot(db, chat_id):
    chat = record(db, Conversation, chat_id)
    # The cursor and message bodies must share one statement snapshot. A later
    # cursor query could acknowledge a delta whose body was not read yet.
    watermark = (
        select(func.coalesce(func.max(Event.id), 0).label("cursor"))
        .where(Event.conversation_id == chat.id)
        .subquery()
    )
    message_rows = db.execute(
        select(Message, watermark.c.cursor)
        .select_from(watermark)
        .outerjoin(Message, Message.conversation_id == chat.id)
        .order_by(Message.created_at, Message.id)
        .execution_options(populate_existing=True)
    ).all()
    event_cursor = message_rows[0][1]
    messages = [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "status": m.status,
            "blocks": m.blocks,
            "createdAt": m.created_at,
        }
        for m, _ in message_rows
        if m is not None
    ]
    project = project_for(db, chat.id)
    cards, runs, reports = [], [], []
    if project:
        cards = [
            {
                "id": c.id,
                "version": c.version,
                "revision": c.revision,
                "status": c.status,
                **c.content,
            }
            for c in db.scalars(
                select(Card).where(Card.project_id == project.id).order_by(Card.version)
            )
        ]
        runs = [
            run_value(db, r) for r in db.scalars(select(Run).where(Run.project_id == project.id))
        ]
        reports = [
            report_value(db, r)
            for r in db.scalars(select(Report).where(Report.project_id == project.id))
        ]
    agents = list(
        db.scalars(
            select(AgentSession)
            .where(AgentSession.conversation_id == chat.id)
            .order_by(AgentSession.created_at.desc())
        )
    )
    active = next((a for a in agents if a.status in {"QUEUED", "RUNNING"}), None)
    status = "waiting_user"
    if cards and cards[-1]["status"] == "ready":
        status = "ready"
    elif any(r["status"] == "PAUSED" for r in runs):
        status = "PAUSED"
    elif agents and agents[0].status == "RECOVERABLE_ERROR":
        status = "recoverable_error"
    elif any(r["status"] == "RUNNING" for r in runs):
        status = "executing"
    elif reports:
        status = (
            "preparing_artifacts"
            if any(r["exportStatus"] in {"WAITING", "QUEUED"} for r in reports)
            else "completed"
        )
    from trustrepro.services.lifecycle import automation
    from trustrepro.services.preferences import public_bindings

    current = active or (agents[0] if agents else None)
    control = automation(db, current)
    if current and current.status not in {"COMPLETED", "WAITING_USER"}:
        status = control["state"]
    elif active:
        status = active.state.get("phase", "thinking")

    return {
        "id": chat.id,
        "title": chat.title,
        "project": {"id": project.id, "title": project.title, "goal": project.goal}
        if project
        else None,
        "status": status,
        "messages": messages,
        "sources": [
            s.content for s in db.scalars(select(Source).where(Source.conversation_id == chat.id))
        ],
        "projectId": project.id if project else None,
        "cards": cards,
        "card": cards[-1] if cards and cards[-1]["status"] not in {"revising", "pending"} else None,
        "proposalState": cards[-1]["status"] if cards else None,
        "runs": runs,
        "reports": reports,
        "allowance": budget(db, chat.account_id, "report" if reports else "execution")
        if db.get(Account, chat.account_id).confirmed
        else None,
        "researchUsage": budget(db, chat.account_id, "research"),
        "modelBindings": public_bindings(db, chat),
        "composerState": composer_state(db, current),
        "error": agents[0].error if agents else None,
        "automation": control,
        "questions": [q for a in agents for q in automation(db, a)["questions"]],
        "eventCursor": event_cursor,
    }


def composer_state(db, agent):
    if not agent:
        return "idle"
    run = db.get(Run, agent.run_id) if agent.run_id else None
    from trustrepro.services.lifecycle import unfinished_jobs

    if agent.state.get("exportPaused"):
        return "pausing" if unfinished_jobs(db, agent) else "paused"
    if (run and run.status == "PAUSING") or (
        agent.desired_state == "PAUSED" and agent.status == "RUNNING"
    ):
        return "pausing"
    if agent.desired_state == "PAUSED":
        return "paused"
    if agent.status in {"QUEUED", "RUNNING", "WAITING_JOB", "RECOVERING"}:
        return "working"
    if unfinished_jobs(db, agent):
        return "working"
    return "idle"


def project_budget(db, project):
    account = db.get(Account, db.get(Conversation, project.conversation_id).account_id)
    return budget(db, account.id, "report") if account.confirmed else None


def adjust_budget(db, project_id, key, max_cost, max_hours, scope="all"):
    from trustrepro.services import lifecycle

    project = record(db, Project, project_id)
    chat = db.get(Conversation, project.conversation_id, with_for_update=True)
    account = db.get(Account, chat.account_id, with_for_update=True)
    if not account.confirmed:
        raise BusinessError("APPROVAL_REQUIRED", "请在任务卡中设置并批准额度")
    old = db.scalar(select(Grant).where(Grant.client_key == key))
    if old:
        if (old.account_id, old.scope, float(old.max_cost), float(old.max_hours)) != (
            account.id,
            scope,
            max_cost,
            max_hours,
        ):
            raise BusinessError("IDEMPOTENCY_CONFLICT", "请求标识已用于其他额度")
        return budget(db, account.id, "report" if scope == "report" else "execution")
    value = budget(db, account.id, "report" if scope == "report" else "execution")
    if max_cost < value["maxCost"] or max_hours < value["maxHours"]:
        raise BusinessError("INVALID_BUDGET", "追加额度不能降低现有上限")
    grant = Grant(
        id=new_id("grant"),
        account_id=account.id,
        scope=scope,
        max_cost=max_cost,
        max_hours=max_hours,
        client_key=key,
    )
    db.add(grant)
    db.flush()
    for agent in db.scalars(select(AgentSession).where(AgentSession.account_id == account.id)):
        lifecycle.emit(
            db,
            f"budget:{grant.id}:{agent.id}",
            "budget_updated",
            session_id=agent.id,
            payload={"scope": scope},
        )
    event(db, chat.id)
    return budget(db, account.id, "report" if scope == "report" else "execution")


def control_receipt(db, chat_id, key, action):
    if not key:
        return False
    db.get(Conversation, chat_id, with_for_update=True)
    identifier = f"control-receipt:{chat_id}:{key}"
    old = db.get(RuntimeEvent, identifier)
    if old:
        if old.payload != {"action": action}:
            raise BusinessError("IDEMPOTENCY_CONFLICT", "操作标识已用于其他请求")
        return True
    db.add(
        RuntimeEvent(
            id=identifier, kind="control_receipt", payload={"action": action}, processed_at=now_ms()
        )
    )
    return False


def control_run(db, run_id, action, client_key=None):
    from trustrepro.services import lifecycle

    run = record(db, Run, run_id)
    chat = record(db, Conversation, record(db, Project, run.project_id).conversation_id)
    if control_receipt(db, chat.id, client_key, action):
        return
    if action not in {"pause", "resume", "cancel"}:
        raise BusinessError("INVALID_ACTION", "操作无效")
    if run.status in {"COMPLETED", "FAILED", "CANCELLED"}:
        return
    agent = session_for(db, chat, "execution", run)
    if action == "resume":
        require_budget(db, run.account_id)
        lifecycle.request_resume(db, agent, new_id("resume"))
    else:
        agent.desired_state = "PAUSED" if action == "pause" else "STOPPED"
        agent.state = {
            **agent.state,
            "resumeRequested": False,
            "controlVersion": agent.state.get("controlVersion", 0) + 1,
        }
        run.status = "PAUSING" if action == "pause" else "CANCELLING"
        run.authorization_version += 1
        if agent.status != "RUNNING":
            agent.status = "PAUSED" if action == "pause" else "CANCELLED"
            lifecycle.phase(agent, agent.status.lower())
        for job in db.scalars(
            select(Job).where(
                Job.run_id == run.id, Job.status.in_(["QUEUED", "RUNNING", "RETRYING"])
            )
        ):
            job.cancel_requested = True
            if job.status == "RETRYING":
                job.status, job.finished_at = "CANCELLED", now_ms()
    lifecycle.emit(db, new_id("control"), "control", session_id=agent.id)
    event(db, chat.id)


def control_conversation(db, conversation_id, action, client_key=None):
    from trustrepro.services import lifecycle

    chat = record(db, Conversation, conversation_id)
    if control_receipt(db, chat.id, client_key, action):
        return
    candidates = list(
        db.scalars(
            select(AgentSession)
            .where(AgentSession.conversation_id == chat.id)
            .order_by(AgentSession.created_at.desc())
            .with_for_update()
        )
    )
    agent = next(
        (a for a in candidates if a.status in {"RUNNING", "QUEUED"}),
        candidates[0] if candidates else None,
    )
    if not agent:
        raise BusinessError("INVALID_ACTION", "没有可控制的会话")
    if lifecycle.finished(db, agent) and (
        lifecycle.unfinished_jobs(db, agent) or agent.state.get("exportPaused")
    ):
        if action == "resume":
            require_budget(db, chat.account_id, "report")
            lifecycle.request_resume(db, agent, new_id("resume"))
        elif action == "pause":
            agent.desired_state, agent.status = "PAUSED", "PAUSED"
            agent.state = {
                **agent.state,
                "exportPaused": True,
                "resumeRequested": False,
                "controlVersion": agent.state.get("controlVersion", 0) + 1,
            }
            for job in db.scalars(
                select(Job).where(
                    Job.run_id == agent.run_id,
                    Job.kind == "export",
                    Job.status.in_(["QUEUED", "RUNNING", "RETRYING"]),
                )
            ):
                job.cancel_requested = True
                if job.status == "RETRYING":
                    job.status = "CANCELLED"
                    db.get(Report, job.arguments["reportId"]).export_status = "PAUSED"
        else:
            raise BusinessError("INVALID_ACTION", "当前只可暂停或继续导出")
        event(db, chat.id)
        return
    if agent.kind == "execution":
        control_run(db, agent.run_id, action)
        return
    if action == "resume":
        require_budget(db, chat.account_id, agent.kind)
        lifecycle.request_resume(db, agent, new_id("resume"))
    elif action in {"pause", "cancel"}:
        agent.desired_state = "PAUSED" if action == "pause" else "STOPPED"
        agent.state = {
            **agent.state,
            "resumeRequested": False,
            "controlVersion": agent.state.get("controlVersion", 0) + 1,
        }
        if agent.status != "RUNNING":
            agent.status = "PAUSED" if action == "pause" else "CANCELLED"
            lifecycle.phase(agent, agent.status.lower())
        for job in db.scalars(
            select(Job).where(
                Job.session_id == agent.id,
                Job.status.in_(["QUEUED", "RUNNING", "RETRYING"]),
            )
        ):
            job.cancel_requested = True
            if job.status == "RETRYING":
                job.status, job.finished_at = "CANCELLED", now_ms()
    else:
        raise BusinessError("INVALID_ACTION", "操作无效")
    lifecycle.emit(db, new_id("control"), "control", session_id=agent.id)
    event(db, chat.id)


def grant_report_time(db, run, client_key, max_hours):
    if max_hours is None:
        return
    old = db.scalar(select(Grant).where(Grant.client_key == client_key))
    if old and (old.account_id != run.account_id or float(old.max_hours) != max_hours):
        raise BusinessError("IDEMPOTENCY_CONFLICT", "授权标识已用于其他额度")
    if not old:
        db.add(
            Grant(
                id=new_id("grant"),
                account_id=run.account_id,
                scope="report",
                max_hours=max_hours,
                client_key=client_key,
            )
        )
        db.flush()


def request_report(db, run_id, client_key, max_hours=None, source_report=None):
    run = record(db, Run, run_id)
    key = f"report-request:{client_key}"
    args = {
        "maxHours": max_hours,
        "sourceReportId": source_report,
    }
    old = db.scalar(select(Job).where(Job.operation_key == key))
    if old:
        if old.run_id != run.id or old.arguments != args:
            raise BusinessError("IDEMPOTENCY_CONFLICT", "报告请求标识已用于其他请求")
        return old.result
    grant_report_time(db, run, client_key, max_hours)
    require_budget(db, run.account_id, "report")
    chat = record(db, Conversation, record(db, Project, run.project_id).conversation_id)
    agent = db.scalar(
        select(AgentSession).where(
            AgentSession.run_id == run.id,
            AgentSession.kind == "report",
            AgentSession.status.in_(["QUEUED", "RUNNING"]),
        )
    )
    if agent is None:
        agent = session_for(db, chat, "report", run, client_key)
    result = {"sessionId": agent.id, "status": agent.status}
    db.add(
        Job(
            id=new_id("job"),
            operation_key=key,
            account_id=run.account_id,
            run_id=run.id,
            scope="report",
            kind="report_request",
            arguments=args,
            status="COMPLETED",
            result=result,
            started_at=now_ms(),
            finished_at=now_ms(),
        )
    )
    event(db, chat.id)
    return result


def report_retry(db, report_id, client_key, max_hours=None, regenerate=False):
    report = record(db, Report, report_id)
    run = record(db, Run, report.run_id)
    if regenerate:
        return request_report(db, run.id, client_key, max_hours, report.id)
    grant_report_time(db, run, client_key, max_hours)
    from trustrepro.services.jobs import enqueue

    job = enqueue(
        db,
        run.account_id,
        run.id,
        "export",
        {"reportId": report.id},
        f"export-retry:{client_key}",
        scope="report",
    )
    report.export_job_id = job.id
    report.export_status = (
        "READY"
        if job.status == "COMPLETED"
        else "FAILED"
        if job.status in {"FAILED", "CANCELLED", "INTERRUPTED"}
        else "QUEUED"
    )
    report.error = job.error
    event(db, record(db, Project, run.project_id).conversation_id)
    return {"jobId": job.id, "status": job.status}


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2).encode()
