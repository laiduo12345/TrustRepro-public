"""Stable operations independent from any particular Runner connection."""

import asyncio

from sqlalchemy import case, func, select

from trustrepro.domain.errors import BusinessError
from trustrepro.infrastructure.db.models import Artifact, Job, Plan, Run, new_id, now_ms
from trustrepro.infrastructure.text import visible_nul
from trustrepro.services.runtime import TERMINAL, activity, fence, require_budget, require_run


def command_error(kind, result):
    code = result.get("exitCode") if isinstance(result, dict) else None
    if kind in {"command", "parse_pdf"} and code is not None and code != 0:
        return f"命令执行失败（退出码 {code}）"
    return None


def status_value(row):
    # Old records also expose command failures without rewriting their history.
    if row.status == "COMPLETED" and command_error(row.kind, row.result):
        return "FAILED"
    return row.status


def error_value(row):
    return row.error or (
        command_error(row.kind, row.result) if row.status in {"COMPLETED", "FAILED"} else None
    )


def status_expression():
    return case(
        (
            (Job.status == "COMPLETED")
            & Job.kind.in_(["command", "parse_pdf"])
            & (Job.result["exitCode"].as_integer() != 0),
            "FAILED",
        ),
        else_=Job.status,
    )


def enqueue(db, account_id, run_id, kind, arguments, key, *, scope="execution", session_id=None):
    old = db.scalar(select(Job).where(Job.operation_key == key))
    if old:
        if (old.account_id, old.run_id, old.kind, old.arguments) != (
            account_id,
            run_id,
            kind,
            arguments,
        ):
            raise BusinessError("IDEMPOTENCY_CONFLICT", "操作标识已用于其他请求")
        return old
    require_budget(db, account_id, scope)
    run = require_run(db, run_id, scope) if run_id else None
    reservation = (
        len(arguments.get("content", "").encode())
        if kind == "write_file"
        else sum(len(c.get("new", "").encode()) for c in arguments.get("changes", []))
    )
    if kind == "import":
        artifact = db.get(Artifact, arguments["artifactId"])
        if artifact is None or artifact.project_id != run.project_id:
            raise BusinessError("SCOPE_DENIED", "产物不属于当前项目")
        reservation = artifact.size
    if run and reservation:
        run = db.get(Run, run_id, with_for_update=True)
        queued = (
            db.scalar(
                select(func.sum(Job.reserved_bytes)).where(
                    Job.run_id == run_id, Job.status.in_(["QUEUED", "RUNNING"])
                )
            )
            or 0
        )
        cap = db.get(Plan, run.plan_id).content["allocation"]["resources"]["diskGb"] * 1024**3
        if run.disk_bytes + queued + reservation > cap:
            raise BusinessError("DISK_BUDGET_EXCEEDED", "运行磁盘额度不足")
    row = Job(
        id=new_id("job"),
        operation_key=key,
        account_id=account_id,
        run_id=run_id,
        reserved_bytes=reservation,
        session_id=session_id,
        kind=kind,
        arguments=arguments,
        scope=scope,
        authorization_version=run.authorization_version if run else 1,
    )
    db.add(row)
    db.flush()
    return row


async def wait(factory, identifier):
    while True:
        with factory() as db:
            row = db.get(Job, identifier)
            if row.status in TERMINAL:
                return {
                    "jobId": row.id,
                    "status": status_value(row),
                    "result": row.result,
                    "error": error_value(row),
                }
        await asyncio.sleep(0.5)


def complete(db, identifier, owner, version, result=None, error=None, status=None):
    row = db.get(Job, identifier, with_for_update=True)
    fence(row, owner, version)
    if row.status != "RUNNING":
        raise BusinessError("JOB_FINISHED", "作业已结束")
    if row.cancel_requested:
        status = "CANCELLED"
    else:
        require_budget(db, row.account_id, row.scope)
        if row.run_id:
            require_run(db, row.run_id, row.scope, row.authorization_version)
    if status in {None, "COMPLETED"}:
        error = error or command_error(row.kind, result)
        status = "FAILED" if error else "COMPLETED"
    row.status = status
    row.result, row.error, row.finished_at = visible_nul(result), visible_nul(error), now_ms()
    activity(db, row, "job", stop=True)
    from trustrepro.services.lifecycle import emit

    emit(
        db,
        "job-done:" + row.id,
        "job_done",
        session_id=row.session_id,
        run_id=row.run_id,
        payload={"jobId": row.id},
    )
    if row.run_id:
        from trustrepro.infrastructure.db.models import Project
        from trustrepro.services.application import event, record

        run = db.get(Run, row.run_id)
        event(db, record(db, Project, run.project_id).conversation_id)
    return row
