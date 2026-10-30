"""Read models for the desktop workspace. No lifecycle transitions or model calls."""

from collections import defaultdict
from math import ceil

from sqlalchemy import String, case, cast, func, literal, or_, select
from sqlalchemy.orm import aliased

from trustrepro.domain.runtime import merged_ms
from trustrepro.infrastructure.db.models import (
    Account,
    Activity,
    AgentSession,
    Artifact,
    Card,
    Conversation,
    Event,
    Job,
    ModelRequest,
    Plan,
    Project,
    Report,
    Run,
    Source,
    UserQuestion,
    now_ms,
)
from trustrepro.services import application as app
from trustrepro.services.jobs import status_expression
from trustrepro.services.lifecycle import automation
from trustrepro.services.runtime import budgets

VISIBLE_JOBS = ("command", "write_file", "apply_patch", "publish", "import", "export")


def _latest(model, partition, *order, where=None):
    query = select(
        model.id.label("id"),
        partition.label("ownerId"),
        func.row_number().over(partition_by=partition, order_by=order).label("rank"),
    )
    return (query.where(where) if where is not None else query).subquery()


def _agent_state(agent):
    return case(
        (
            agent.status.in_(["COMPLETED", "CANCELLED", "PAUSED", "BLOCKED"]),
            func.lower(agent.status),
        ),
        else_=func.coalesce(agent.state["phase"].as_string(), func.lower(agent.status)),
    )


def _title(result, fallback):
    return func.substr(
        func.coalesce(func.nullif(result["title"].as_string(), ""), fallback), 1, 180
    )


def _run_query():
    ranked = _latest(
        AgentSession,
        AgentSession.run_id,
        AgentSession.created_at.desc(),
        AgentSession.id,
        where=AgentSession.kind == "execution",
    )
    agent = aliased(AgentSession)
    return (
        select(
            Run.id,
            Run.project_id.label("projectId"),
            Run.plan_id.label("planId"),
            Run.account_id.label("accountId"),
            Run.status,
            Run.error,
            Run.created_at.label("createdAt"),
            Run.finished_at.label("finishedAt"),
            Project.title.label("projectTitle"),
            Project.conversation_id.label("conversationId"),
            _title(Plan.content, Run.id).label("title"),
            func.coalesce(_agent_state(agent), Run.status).label("displayStatus"),
        )
        .select_from(Run)
        .join(Plan, Plan.id == Run.plan_id)
        .join(Project, Project.id == Run.project_id)
        .outerjoin(ranked, (ranked.c.ownerId == Run.id) & (ranked.c.rank == 1))
        .outerjoin(agent, agent.id == ranked.c.id)
    )


def _report_query():
    return (
        select(
            Report.id,
            Report.project_id.label("projectId"),
            Report.run_id.label("runId"),
            Report.version,
            Report.created_at.label("createdAt"),
            Report.export_status.label("exportStatus"),
            _title(Report.result, "复现报告").label("title"),
            Project.title.label("projectTitle"),
        )
        .select_from(Report)
        .join(Project, Project.id == Report.project_id)
    )


def _project_query():
    changed = (
        select(Event.conversation_id, func.max(Event.created_at).label("stamp"))
        .group_by(Event.conversation_id)
        .subquery()
    )
    runs = _run_query().subquery()
    # Prefer a live run; otherwise show the most recent run, as in the existing workbench.
    rank = select(
        runs.c.id,
        runs.c.projectId,
        runs.c.displayStatus,
        func.row_number()
        .over(
            partition_by=runs.c.projectId,
            order_by=(
                case(
                    (runs.c.status.in_(["RUNNING", "QUEUED", "PAUSING", "CANCELLING"]), 0), else_=1
                ),
                runs.c.createdAt.desc(),
                runs.c.id,
            ),
        )
        .label("rank"),
    ).subquery()
    pending = (
        select(Card.project_id)
        .where(Card.project_id == Project.id, Card.status == "ready")
        .exists()
    )
    return (
        select(
            Project.id,
            Project.title,
            Project.goal,
            Project.conversation_id.label("conversationId"),
            Project.created_at.label("createdAt"),
            Conversation.account_id.label("accountId"),
            func.coalesce(changed.c.stamp, Project.created_at, Conversation.created_at).label(
                "updatedAt"
            ),
            case(
                (pending, "ready"), else_=func.coalesce(rank.c.displayStatus, "waiting_user")
            ).label("status"),
        )
        .join(Conversation, Conversation.id == Project.conversation_id)
        .outerjoin(changed, changed.c.conversation_id == Project.conversation_id)
        .outerjoin(rank, (rank.c.projectId == Project.id) & (rank.c.rank == 1))
    )


def _page(db, query, page, size, field, order, stats):
    base = query.subquery()
    total = db.scalar(select(func.count()).select_from(base)) or 0
    page = min(page, max(1, ceil(total / size)))
    column = base.c[field]
    ordering = column.asc().nulls_last() if order == "asc" else column.desc().nulls_last()
    rows = [
        dict(r)
        for r in db.execute(
            select(base).order_by(ordering, base.c.id).offset((page - 1) * size).limit(size)
        ).mappings()
    ]
    return {"items": rows, "total": total, "page": page, "pageSize": size, "stats": stats}


def _counts(db, query, status):
    base = query.subquery()
    values = dict(db.execute(select(base.c[status], func.count()).group_by(base.c[status])).all())
    return {"total": sum(values.values()), **values}


def _search(query, columns, text):
    if text.strip():
        needle = (
            "%" + text.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        )
        query = query.where(or_(*(c.ilike(needle, escape="\\") for c in columns)))
    return query


def _report_values(db, rows):
    # Counts are projected without loading the report's result or artifacts.
    ids = [r["id"] for r in rows]
    lengths = (
        {
            i: len(a)
            for i, a in db.execute(select(Report.id, Report.artifact_ids).where(Report.id.in_(ids)))
        }
        if ids
        else {}
    )
    return [{**r, "artifactCount": lengths.get(r["id"], 0)} for r in rows]


def projects(db, *, page=1, pageSize=10, q="", status="all", order="desc"):
    base = _project_query()
    stats = _counts(db, base, "status")
    query = _search(base, [Project.title, Project.goal], q)
    if status != "all":
        sub = query.subquery()
        query = select(sub).where(sub.c.status == status)
    result = _page(db, query, page, pageSize, "updatedAt", order, stats)
    _project_values(db, result["items"])
    return result


def _project_values(db, rows):
    ids = [r["id"] for r in rows]
    if not ids:
        return
    run_counts = dict(
        db.execute(
            select(Run.project_id, func.count())
            .where(Run.project_id.in_(ids))
            .group_by(Run.project_id)
        ).all()
    )
    report_counts = dict(
        db.execute(
            select(Report.project_id, func.count())
            .where(Report.project_id.in_(ids))
            .group_by(Report.project_id)
        ).all()
    )
    accounts = {
        a.id: a.confirmed
        for a in db.scalars(select(Account).where(Account.id.in_([r["accountId"] for r in rows])))
    }
    allowances = budgets(db, list(accounts), "report")
    for row in rows:
        account = row.pop("accountId")
        row.update(
            runCount=run_counts.get(row["id"], 0),
            reportCount=report_counts.get(row["id"], 0),
            allowance=allowances.get(account) if accounts[account] else None,
        )


def project(db, identifier):
    app.record(db, Project, identifier)
    row = dict(db.execute(_project_query().where(Project.id == identifier)).mappings().one())
    _project_values(db, [row])
    return row


def runs(
    db, *, projectId=None, page=1, pageSize=10, q="", status="all", order="desc", hasReports=False
):
    base = _run_query()
    if projectId:
        app.record(db, Project, projectId)
        base = base.where(Run.project_id == projectId)
    stats = _counts(db, base, "displayStatus")
    query = _search(
        base,
        [
            Run.id,
            Project.title,
            Plan.content["title"].as_string(),
            cast(Plan.content["parameters"], String),
        ],
        q,
    )
    if q.strip():
        # Include matching report titles without joining them into duplicate run rows.
        report_matches = _search(
            select(Report.run_id), [Report.id, Report.result["title"].as_string()], q
        )
        ids = query.with_only_columns(Run.id).order_by(None)
        query = base.where(or_(Run.id.in_(ids), Run.id.in_(report_matches)))
    if hasReports:
        query = query.where(select(Report.id).where(Report.run_id == Run.id).exists())
    if status != "all":
        sub = query.subquery()
        query = select(sub).where(or_(sub.c.displayStatus == status, sub.c.status == status))
    result = _page(db, query, page, pageSize, "createdAt", order, stats)
    result["items"] = _run_values(db, result["items"])
    return result


def _run_values(db, rows):
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    agents = list(
        db.scalars(
            select(AgentSession)
            .where(AgentSession.run_id.in_(ids))
            .order_by(AgentSession.created_at.desc(), AgentSession.id)
        )
    )
    questions = defaultdict(list)
    for question in db.scalars(
        select(UserQuestion).where(
            UserQuestion.session_id.in_([a.id for a in agents]), UserQuestion.answers.is_(None)
        )
    ):
        questions[question.session_id].append(question)
    execution = {}
    owners = {"agent:" + a.id: a.run_id for a in agents}
    for agent in agents:
        if agent.kind == "execution":
            execution.setdefault(agent.run_id, agent)
    jobs = defaultdict(list)
    for job in db.execute(
        select(
            Job.id,
            Job.run_id,
            Job.kind,
            status_expression().label("status"),
            Job.created_at,
            Job.started_at,
            Job.finished_at,
            func.substr(Job.arguments["command"].as_string(), 1, 512).label("command"),
        )
        .where(Job.run_id.in_(ids))
        .order_by(Job.created_at, Job.id)
    ).mappings():
        jobs[job["run_id"]].append(dict(job))
        owners["job:" + job["id"]] = job["run_id"]
    intervals, costs = defaultdict(list), {}
    stamp = now_ms()
    for key, start, end, lease in db.execute(
        select(Activity.id, Activity.started_at, Activity.ended_at, Activity.lease_until).where(
            Activity.account_id.in_([r["accountId"] for r in rows]),
            Activity.usage_scope == "reproduction",
        )
    ):
        owner = ":".join(key.split(":")[:2])
        if owner in owners:
            intervals[owners[owner]].append((start, end if end is not None else min(stamp, lease)))
    for identifier, spent, reserved in db.execute(
        select(
            AgentSession.run_id,
            func.coalesce(func.sum(ModelRequest.actual_cost), 0),
            func.coalesce(
                func.sum(
                    case(
                        (
                            ModelRequest.status.in_(["REQUESTED", "UNCERTAIN"]),
                            ModelRequest.reserved_cost,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
        )
        .join(AgentSession, AgentSession.id == ModelRequest.session_id)
        .where(AgentSession.run_id.in_(ids), ModelRequest.usage_scope == "reproduction")
        .group_by(AgentSession.run_id)
    ):
        costs[identifier] = (float(spent), float(reserved))
    reports = defaultdict(list)
    for report in _report_values(
        db,
        [
            dict(r)
            for r in db.execute(
                _report_query()
                .where(Report.run_id.in_(ids))
                .order_by(Report.version.desc(), Report.id)
            ).mappings()
        ],
    ):
        reports[report["runId"]].append(report)
    for row in rows:
        identifier = row["id"]
        row.pop("accountId")
        agent = execution.get(identifier)
        entries = [j for j in jobs[identifier] if j["kind"] in VISIBLE_JOBS]
        commands = [j for j in entries if j["kind"] == "command"]
        current = next(
            (j for j in reversed(commands) if j["status"] in {"RUNNING", "QUEUED"}),
            commands[-1] if commands else None,
        )
        spent, reserved = costs.get(identifier, (0, 0))
        row.update(
            automation=automation(db, agent, questions.get(agent.id, [])) if agent else None,
            jobCount=len(entries),
            completedJobs=sum(j["status"] == "COMPLETED" for j in entries),
            commandCount=len(commands),
            completedCommands=sum(j["status"] == "COMPLETED" for j in commands),
            currentJob={
                "id": current["id"],
                "status": current["status"],
                "command": current["command"],
                "createdAt": current["created_at"],
                "finishedAt": current["finished_at"],
            }
            if current
            else None,
            reportCount=len(reports[identifier]),
            latestReport=next(iter(reports[identifier]), None),
            usage={
                "currency": "CNY",
                "spentCost": spent,
                "reservedCost": reserved,
                "elapsedSeconds": merged_ms(intervals[identifier]) / 1000,
            },
        )
    return rows


def run_overview(db, identifier):
    app.record(db, Run, identifier)
    row = dict(db.execute(_run_query().where(Run.id == identifier)).mappings().one())
    row = _run_values(db, [row])[0]
    plan = app.record(db, Plan, row["planId"])
    row["approvedPlan"] = plan.content
    row["approvedAt"] = plan.approved_at
    return row


def reports(
    db,
    *,
    projectId=None,
    runId=None,
    page=1,
    pageSize=10,
    q="",
    status="all",
    order="desc",
    includeHistory=False,
    beforeVersion=None,
):
    base = _report_query()
    if projectId:
        app.record(db, Project, projectId)
        base = base.where(Report.project_id == projectId)
    if runId:
        run = app.record(db, Run, runId)
        if projectId and run.project_id != projectId:
            from trustrepro.domain.errors import BusinessError

            raise BusinessError("NOT_FOUND", "运行不属于当前项目")
        base = base.where(Report.run_id == runId)
    all_count = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    latest = _latest(Report, Report.run_id, Report.version.desc(), Report.id)
    current = base.where(Report.id.in_(select(latest.c.id).where(latest.c.rank == 1)))
    stats = _counts(db, current, "exportStatus")
    stats["history"] = all_count - stats["total"]
    query = _search(
        base if includeHistory else current,
        [
            Project.title,
            Report.id,
            Report.run_id,
            Report.result["title"].as_string(),
            literal("v") + cast(Report.version, String),
        ],
        q,
    )
    if status != "all":
        query = query.where(Report.export_status == status)
    if beforeVersion is not None:
        query = query.where(Report.version < beforeVersion)
    result = _page(db, query, page, pageSize, "createdAt", order, stats)
    result["items"] = _report_values(db, result["items"])
    return result


def run_jobs(db, identifier, *, page=1, pageSize=10, kind="all", order="desc"):
    app.record(db, Run, identifier)
    query = select(
        Job.id,
        Job.run_id.label("runId"),
        Job.kind,
        status_expression().label("status"),
        Job.created_at.label("createdAt"),
        Job.started_at.label("startedAt"),
        Job.finished_at.label("finishedAt"),
        func.substr(
            func.coalesce(Job.arguments["command"].as_string(), Job.arguments["path"].as_string()),
            1,
            512,
        ).label("command"),
    ).where(Job.run_id == identifier, Job.kind.in_(VISIBLE_JOBS))
    stats = _counts(db, query, "status")
    if kind != "all":
        query = query.where(Job.kind == "command" if kind == "command" else Job.kind != "command")
    return _page(db, query, page, pageSize, "createdAt", order, stats)


def sources(db, identifier):
    app.record(db, Conversation, identifier)
    return [
        row.content
        for row in db.scalars(
            select(Source).where(Source.conversation_id == identifier).order_by(Source.id)
        )
    ]


def artifacts(db, identifier, run_id=None):
    app.record(db, Project, identifier)
    query = select(Artifact).where(Artifact.project_id == identifier)
    if run_id:
        run = app.record(db, Run, run_id)
        if run.project_id != identifier:
            from trustrepro.domain.errors import BusinessError

            raise BusinessError("NOT_FOUND", "运行不属于当前项目")
        query = query.where(Artifact.run_id == run_id)
    return [
        app.artifact_value(row)
        for row in db.scalars(query.order_by(Artifact.created_at.desc(), Artifact.id))
    ]
