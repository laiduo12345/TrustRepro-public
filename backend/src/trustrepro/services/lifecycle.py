"""Durable task progression. Scientific work remains inside the native harness."""

import hashlib
import json
import random

from sqlalchemy import select

from trustrepro.domain.errors import BusinessError
from trustrepro.infrastructure.db.models import (
    AgentSession,
    Card,
    Conversation,
    InputDelivery,
    Job,
    ModelRequest,
    Project,
    Run,
    RuntimeEvent,
    UserQuestion,
    now_ms,
)
from trustrepro.services.runtime import activity, require_budget


def emit(db, key, kind, *, session_id=None, run_id=None, payload=None):
    # All supported databases provide INSERT ... ON CONFLICT. An event is never replayed as a write.
    if db.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    db.execute(
        insert(RuntimeEvent)
        .values(
            id=key,
            kind=kind,
            session_id=session_id,
            run_id=run_id,
            payload=payload or {},
            created_at=now_ms(),
        )
        .on_conflict_do_nothing(index_elements=["id"])
    )


def refresh(db, row):
    from trustrepro.services.application import event

    event(db, row.conversation_id)


def phase(row, value, reason=None):
    row.state = {**row.state, "phase": value}
    row.error = reason


def pending_questions(db, row):
    return list(
        db.scalars(
            select(UserQuestion)
            .where(
                UserQuestion.session_id == row.id,
                UserQuestion.answers.is_(None),
            )
            .order_by(UserQuestion.created_at)
        )
    )


def unfinished_jobs(db, row):
    query = select(Job.id).where(Job.status.in_(["QUEUED", "RUNNING", "RETRYING"]))
    query = (
        query.where(Job.run_id == row.run_id)
        if row.run_id
        else query.where(Job.session_id == row.id)
    )
    return db.scalar(query.limit(1)) is not None


def finished(db, row):
    if row.state.get("completionSubmitted"):
        return True
    if row.kind == "execution" and row.run_id:
        return db.get(Run, row.run_id).status in {"COMPLETED", "FAILED"}
    return False


def block(db, row, reason, budget_limited=False):
    row.status = "BLOCKED"
    phase(row, "blocked", reason)
    activity(db, row, "agent", stop=True)
    if budget_limited:
        row.desired_state = "BUDGET_LIMITED"
        if row.run_id:
            for job in db.scalars(
                select(Job).where(
                    Job.run_id == row.run_id,
                    Job.scope == ("report" if row.kind == "report" else "execution"),
                    Job.status.in_(["QUEUED", "RUNNING", "RETRYING"]),
                )
            ):
                job.cancel_requested = True
                if job.status == "RETRYING":
                    job.status, job.finished_at = "CANCELLED", now_ms()
    refresh(db, row)


def queue(db, row):
    if row.desired_state != "ACTIVE" or row.status == "RUNNING":
        return
    if finished(db, row):
        row.status = "COMPLETED"
        phase(row, "completed")
        return
    if any(q.blocking for q in pending_questions(db, row)):
        row.status = "WAITING_USER"
        phase(row, "waiting_user")
        return
    try:
        require_budget(db, row.account_id, row.kind)
    except BusinessError as exc:
        block(db, row, exc.message, budget_limited=True)
        return
    if unfinished_jobs(db, row):
        row.status = "WAITING_JOB"
        phase(row, "waiting_job")
    elif db.scalar(
        select(ModelRequest.id).where(
            ModelRequest.session_id == row.id, ModelRequest.status == "REQUESTED"
        )
    ):
        row.status, row.retry_at = "RECOVERING", now_ms() + 3000
        phase(row, "recovering", "正在确认中断请求的状态")
    elif row.retry_at > now_ms():
        row.status = "RECOVERING"
        phase(row, "recovering", row.error)
    else:
        row.status = "QUEUED"
        phase(row, "thinking")


def backoff(attempt):
    return int(min(60, 2 ** min(attempt, 6)) * random.uniform(0.8, 1.0) * 1000)


def failure_class(error):
    code = getattr(error, "code", "")
    value = str(error).lower()
    if code in {"TIME_BUDGET_EXCEEDED", "COST_BUDGET_EXCEEDED"} or any(
        marker in value for marker in ("time_budget_exceeded", "cost_budget_exceeded")
    ):
        return "budget"
    if code in {
        "INVALID_REFERENCE",
        "INVALID_PATH",
        "SCOPE_DENIED",
        "UNSUPPORTED_PROTOCOL",
        "MODEL_DISABLED",
        "MODEL_MISMATCH",
        "MODEL_CONNECTION_CHANGED",
    } or any(
        v in value
        for v in (
            "unauthorized",
            "invalid_api_key",
            "model_disabled",
            "model_mismatch",
            "model_connection_changed",
            "authentication",
            "unsupported protocol",
            "invalid schema",
            "failed to parse responsecompleted",
            "badrequest",
            "invalid_request_error",
            "corrupt",
            "permission denied",
            "401 unauthorized",
            "403 forbidden",
        )
    ):
        return "permanent"
    return "transient"


def observation(db, row, name, args, result):
    # Re-reading an unchanged observation does not defeat the no-progress guard.
    if name in {"get_budget", "list_jobs", "list_artifacts", "wait_job"}:
        return
    digest = hashlib.sha256(
        json.dumps([name, args, result], ensure_ascii=False, default=str, sort_keys=True).encode()
    ).hexdigest()
    seen = row.state.get("observations", [])
    if digest not in seen:
        row.progress_version += 1
        row.state = {**row.state, "observations": [*seen[-99:], digest]}


def delivery(db, row, identifier, payload):
    existing = db.get(InputDelivery, identifier)
    if existing:
        if existing.session_id != row.id or existing.payload != payload:
            raise BusinessError("IDEMPOTENCY_CONFLICT", "输入标识已用于其他内容")
        return existing
    item = InputDelivery(id=identifier, session_id=row.id, payload=payload)
    db.add(item)
    emit(db, "input:" + identifier, "input", session_id=row.id)
    return item


def request_resume(db, row, key):
    version = row.state.get("controlVersion", 0) + 1
    row.state = {**row.state, "controlVersion": version}
    emit(db, key, "resume", session_id=row.id, payload={"controlVersion": version})


def ask(db, row, params):
    identifier = "question-" + hashlib.sha256((row.id + params["itemId"]).encode()).hexdigest()
    question = db.get(UserQuestion, identifier)
    if question is None:
        question = UserQuestion(
            id=identifier,
            session_id=row.id,
            conversation_id=row.conversation_id,
            item_id=params["itemId"],
            turn_id=params["turnId"],
            blocking=params["isBlocking"],
            questions=params["questions"],
        )
        db.add(question)
        db.flush()
    if question.blocking and question.answers is None:
        phase(row, "waiting_user")
        activity(db, row, "agent", stop=True)
    refresh(db, row)
    return question


def answer(db, identifier, key, answers):
    q = db.get(UserQuestion, identifier, with_for_update=True)
    if q is None:
        raise BusinessError("NOT_FOUND", "问题不存在")
    if q.answers is not None:
        if q.answer_key != key or q.answers != answers:
            raise BusinessError("IDEMPOTENCY_CONFLICT", "问题已有其他回答")
        return
    expected = {v["id"] for v in q.questions}
    if set(answers) != expected or any(
        not isinstance(v, dict)
        or not isinstance(v.get("answers"), list)
        or not all(isinstance(a, str) for a in v["answers"])
        or not v["answers"]
        for v in answers.values()
    ):
        raise BusinessError("INVALID_ARGUMENTS", "请回答所有问题")
    row = db.get(AgentSession, q.session_id, with_for_update=True)
    q.answers, q.answer_key, q.answered_at = answers, key, now_ms()
    item = delivery(
        db,
        row,
        "answer-input-" + q.id,
        {
            "questionId": q.id,
            "nativeItemId": q.item_id,
            "answers": answers,
        },
    )
    if q.blocking and row.status == "RUNNING" and row.lease_until > now_ms():
        item.status = "NATIVE_PENDING"
    row.activity_epoch += 1
    row.idle_turns = 0
    emit(db, "answer:" + q.id, "answer", session_id=row.id, payload={"questionId": q.id})
    refresh(db, row)


def project_needs_research(db, row):
    project = db.scalar(select(Project).where(Project.conversation_id == row.conversation_id))
    latest = (
        db.scalar(select(Card).where(Card.project_id == project.id).order_by(Card.version.desc()))
        if project
        else None
    )
    return project is not None and (latest is None or latest.status not in {"ready", "approved"})


def reduce_event(db, row, event):
    data = event.payload
    if event.kind in {"resume", "budget_updated"}:
        if event.kind == "resume" and data.get("controlVersion") != row.state.get("controlVersion"):
            return
        if event.kind == "budget_updated" and row.desired_state not in {"PAUSED", "STOPPED"}:
            from trustrepro.services.artifacts import resume_budget_exports

            resume_budget_exports(db, row.run_id)
            if row.status != "RUNNING" and finished(db, row):
                row.desired_state, row.status = "ACTIVE", "COMPLETED"
                phase(row, "completed")
                refresh(db, row)
                return
        if (
            event.kind == "budget_updated"
            and data.get("scope") == "report"
            and row.kind != "report"
        ):
            return
        if row.desired_state == "STOPPED" or (
            event.kind == "budget_updated" and row.desired_state != "BUDGET_LIMITED"
        ):
            return
        row.state = {**row.state, "resumeRequested": True}
        resume_when_stopped(db, row)
    elif event.kind in {"input", "answer"}:
        row.idle_turns = 0
        if row.status not in {"RUNNING", "COMPLETED"}:
            row.retry_at = 0
            queue(db, row)
    elif event.kind in {"turn_done", "turn_failed"}:
        if (
            row.owner_version != data["ownerVersion"]
            or row.state.get("endedOwnerVersion", 0) >= data["ownerVersion"]
        ):
            return
        row.state = {**row.state, "endedOwnerVersion": data["ownerVersion"]}
        from trustrepro.services.messages import end_turn

        end_turn(
            db,
            row,
            "completed"
            if event.kind == "turn_done" and row.desired_state == "ACTIVE"
            else "interrupted",
            observed=False,
        )
        activity(db, row, "agent", stop=True)
        row.lease_until, row.lease_token = 0, None
        row.status = "IDLE"
        if event.kind == "turn_done" and row.desired_state == "ACTIVE":
            chat = db.get(Conversation, row.conversation_id, with_for_update=True)
            project = db.scalar(select(Project).where(Project.conversation_id == chat.id))
            if project:
                for card in db.scalars(
                    select(Card).where(
                        Card.project_id == project.id,
                        Card.status == "pending",
                        Card.input_version == chat.input_version,
                    )
                ):
                    card.status = "ready"
        if row.desired_state != "ACTIVE":
            row.status = (
                "PAUSED"
                if row.desired_state == "PAUSED"
                else "BLOCKED"
                if row.desired_state == "BUDGET_LIMITED"
                else "CANCELLED"
            )
            phase(row, row.status.lower(), row.error if row.status == "BLOCKED" else None)
        elif finished(db, row):
            row.status = "COMPLETED"
            phase(row, "completed")
        elif any(q.blocking for q in pending_questions(db, row)):
            queue(db, row)
        elif (
            event.kind == "turn_done"
            and pending_questions(db, row)
            and not unfinished_jobs(db, row)
        ):
            # Default-mode native questions are asynchronous. If the agent ends its work
            # with an outstanding question, wait for its answer instead of paying to ask again.
            row.status = "WAITING_USER"
            phase(row, "waiting_user")
        elif event.kind == "turn_failed":
            if data.get("classification") in {"budget", "permanent"}:
                block(
                    db, row, data["reason"], budget_limited=data.get("classification") == "budget"
                )
            else:
                row.recovery_count += 1
                row.retry_at = now_ms() + backoff(row.recovery_count)
                row.error = data.get("reason", "连接中断，正在恢复")
                row.state = {
                    **row.state,
                    "recovery": {
                        "instruction": "查询原作业和文件后继续；不要重发中断请求或重复已完成实验。"
                    },
                }
                queue(db, row)
        else:
            pending_input = db.scalar(
                select(InputDelivery.id).where(
                    InputDelivery.session_id == row.id, InputDelivery.status == "PENDING"
                )
            )
            if row.kind == "research" and not pending_input and not project_needs_research(db, row):
                row.status = "WAITING_USER"
                phase(row, "waiting_user")
            else:
                automatic = data.get("automatic", False)
                changed = row.progress_version > data.get("progressAtStart", 0)
                row.idle_turns = (
                    row.idle_turns + 1
                    if automatic and not changed and not unfinished_jobs(db, row)
                    else 0
                )
                row.recovery_count, row.retry_at = 0, 0
                if row.idle_turns >= 3:
                    block(
                        db, row, "连续3个自动接续回合没有新行动或结果，已停止空转；已有工作已保留"
                    )
                else:
                    queue(db, row)
    elif event.kind == "job_done":
        row.progress_version += 1
        if row.status in {"WAITING_JOB", "RECOVERING"}:
            queue(db, row)
    elif event.kind == "budget_limit" and row.desired_state == "ACTIVE":
        block(db, row, data["reason"], budget_limited=True)
    refresh(db, row)


def resume_when_stopped(db, row):
    if not row.state.get("resumeRequested") or row.status == "RUNNING" or unfinished_jobs(db, row):
        return
    if row.desired_state == "STOPPED":
        row.state = {**row.state, "resumeRequested": False}
        return
    try:
        require_budget(db, row.account_id, "report" if row.state.get("exportPaused") else row.kind)
    except BusinessError:
        row.state = {**row.state, "resumeRequested": False}
        return
    if row.state.get("exportPaused"):
        from trustrepro.services.artifacts import resume_budget_exports

        row.state = {**row.state, "exportPaused": False, "resumeRequested": False}
        row.desired_state, row.status = "ACTIVE", "COMPLETED"
        resume_budget_exports(db, row.run_id, statuses=("PAUSED",))
        refresh(db, row)
        return
    if row.run_id and row.kind == "execution":
        run = db.get(Run, row.run_id, with_for_update=True)
        if run.status in {"CANCELLED", "CANCELLING", "COMPLETED", "FAILED"}:
            return
        run.status, run.error = "RUNNING", None
    row.desired_state, row.retry_at, row.idle_turns = "ACTIVE", 0, 0
    row.state = {**row.state, "resumeRequested": False}
    queue(db, row)
    refresh(db, row)


def tick(db):
    # Locked inbox rows are processed once; emitted follow-ups remain durable.
    for item in db.scalars(
        select(RuntimeEvent)
        .where(RuntimeEvent.processed_at.is_(None))
        .order_by(RuntimeEvent.created_at)
        .limit(100)
        .with_for_update(skip_locked=True)
    ):
        if item.session_id:
            rows = [db.get(AgentSession, item.session_id, with_for_update=True)]
        else:
            rows = (
                list(
                    db.scalars(
                        select(AgentSession)
                        .where(AgentSession.run_id == item.run_id)
                        .with_for_update()
                    )
                )
                if item.run_id
                else []
            )
        for row in rows:
            if row:
                reduce_event(db, row, item)
        item.processed_at = now_ms()
    for row in db.scalars(
        select(AgentSession)
        .where(AgentSession.status == "RUNNING", AgentSession.lease_until < now_ms())
        .with_for_update(skip_locked=True)
    ):
        emit(
            db,
            f"lost:{row.id}:{row.owner_version}",
            "turn_failed",
            session_id=row.id,
            payload={
                "ownerVersion": row.owner_version,
                "classification": "transient",
                "reason": "执行连接中断，正在恢复原会话",
            },
        )
    for row in db.scalars(
        select(AgentSession)
        .where(
            AgentSession.status.in_(["RECOVERING", "WAITING_JOB"]),
            AgentSession.retry_at <= now_ms(),
        )
        .with_for_update(skip_locked=True)
    ):
        before = row.status
        queue(db, row)
        if row.status != before:
            refresh(db, row)
    for run in db.scalars(select(Run).where(Run.status.in_(["PAUSING", "CANCELLING"]))):
        remaining = db.scalar(
            select(Job.id).where(
                Job.run_id == run.id, Job.status.in_(["RUNNING", "QUEUED", "RETRYING"])
            )
        )
        agent_running = db.scalar(
            select(AgentSession.id).where(
                AgentSession.run_id == run.id, AgentSession.status == "RUNNING"
            )
        )
        if remaining is None and agent_running is None:
            run.status = "PAUSED" if run.status == "PAUSING" else "CANCELLED"
    for row in db.scalars(
        select(AgentSession).where(
            AgentSession.status.in_(["PAUSED", "BLOCKED", "WAITING_JOB", "IDLE"])
        )
    ):
        resume_when_stopped(db, row)
    from trustrepro.services.artifacts import schedule_exports

    for job in db.scalars(
        select(Job)
        .where(Job.status == "RETRYING", Job.retry_at <= now_ms())
        .with_for_update(skip_locked=True)
    ):
        job.status = "QUEUED"
    schedule_exports(db)


def automation(db, row, questions=None):
    if not row:
        return None
    questions = [
        {"id": q.id, "blocking": q.blocking, "questions": q.questions}
        for q in (pending_questions(db, row) if questions is None else questions)
    ]
    status = (
        row.status.lower()
        if row.status in {"COMPLETED", "CANCELLED", "PAUSED", "BLOCKED"}
        else row.state.get("phase") or row.status.lower()
    )
    actions = []
    if row.desired_state == "PAUSED" or row.status == "BLOCKED":
        actions.extend(["resume", "cancel"])
    elif row.desired_state == "ACTIVE" and row.status not in {"COMPLETED", "CANCELLED"}:
        actions.extend(["pause", "cancel"])
    return {
        "state": status,
        "reason": row.error,
        "retryAt": row.retry_at or None,
        "recoveryCount": row.recovery_count,
        "questions": questions,
        "allowedActions": actions,
    }
