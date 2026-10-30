"""Worker lifecycle shared by file jobs and the Docker-owning Manager."""

import asyncio
import logging
import threading

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from trustrepro.domain.errors import BusinessError
from trustrepro.infrastructure.db.models import AgentSession, Job, Report, Run, now_ms
from trustrepro.services import artifacts
from trustrepro.services.application import record
from trustrepro.services.jobs import complete
from trustrepro.services.runtime import (
    claim,
    fence,
    heartbeat,
    require_budget,
    require_run,
)

log = logging.getLogger(__name__)


class Execution:
    def __init__(self, factory, settings, job, owner):
        self.factory, self.settings, self.owner = factory, settings, owner
        self.id, self.version = job.id, job.owner_version
        self.kind, self.run_id = job.kind, job.run_id
        self.cancelled = threading.Event()
        self.failure = None
        self.ownership_lost = False
        self.ownership_deadline = job.lease_until

    def checkpoint(self):
        if self.ownership_lost or now_ms() >= self.ownership_deadline:
            self.ownership_lost = True
            raise BusinessError("OWNER_EXPIRED", "作业所有权已失效，等待新执行者接管")
        if self.cancelled.is_set():
            raise BusinessError("CANCELLED", self.failure or "作业已取消")

    def pulse(self):
        with self.factory.begin() as db:
            row = heartbeat(db, Job, self.id, self.owner, self.version)
            self.ownership_deadline = row.lease_until
            if row.cancel_requested:
                self.cancelled.set()
            try:
                require_budget(db, row.account_id, row.scope)
                if row.run_id:
                    require_run(db, row.run_id, row.scope, row.authorization_version)
            except BusinessError as error:
                self.failure = error.message
                row.progress = {**row.progress, "failureCode": error.code}
                row.cancel_requested = True
                self.cancelled.set()

    def save_handle(self, value):
        with self.factory.begin() as db:
            row = db.get(Job, self.id, with_for_update=True)
            fence(row, self.owner, self.version)
            row.handle = {**row.handle, **value}

    async def execute(self, function):
        task = asyncio.create_task(asyncio.to_thread(function, self))
        while not task.done():
            try:
                self.pulse()
            except SQLAlchemyError:
                pass
            except Exception as error:
                self.failure = str(error)
                if getattr(error, "code", None) == "OWNER_EXPIRED":
                    self.ownership_lost = True
                else:
                    self.cancelled.set()
            await asyncio.wait([task], timeout=2)
        result, error, interrupted = None, None, False
        try:
            result = task.result()
        except Exception as exc:
            error = str(exc)
            interrupted = getattr(exc, "code", None) == "INTERRUPTED"
            log.warning("Job %s: %s", self.id, error)
        while True:
            try:
                self.commit_result(result, error, interrupted)
                return
            except SQLAlchemyError:
                if now_ms() >= self.ownership_deadline:
                    return
                await asyncio.sleep(1)

    def commit_result(self, result, error, interrupted):
        with self.factory.begin() as db:
            row = db.get(Job, self.id, with_for_update=True)
            try:
                fence(row, self.owner, self.version)
            except BusinessError:
                return
            status = (
                "CANCELLED"
                if row.cancel_requested or self.cancelled.is_set()
                else "INTERRUPTED"
                if interrupted
                else None
            )
            try:
                require_budget(db, row.account_id, row.scope)
                if row.run_id:
                    require_run(db, row.run_id, row.scope, row.authorization_version)
            except BusinessError as exc:
                status, error = "CANCELLED", str(exc)
                row.progress = {**row.progress, "failureCode": exc.code}
                row.cancel_requested = True
            if row.kind == "publish" and result and status is None and error is None:
                result = artifacts.save_published(db, result)
            if row.kind == "export":
                report = record(db, Report, row.arguments["reportId"])
                if error and not status:
                    from trustrepro.services.lifecycle import backoff, failure_class

                    if failure_class(RuntimeError(error)) == "transient":
                        from trustrepro.services.runtime import activity

                        row.retry_count += 1
                        row.retry_at = now_ms() + backoff(row.retry_count)
                        row.status, row.error = "RETRYING", error
                        report.export_status, report.error = (
                            "RETRYING",
                            "产物包生成中断，正在自动重试",
                        )
                        activity(db, row, "job", stop=True)
                        return
                report.export_status = "FAILED" if error or status else "READY"
                if status and any(
                    a.state.get("exportPaused")
                    for a in db.scalars(
                        select(AgentSession).where(AgentSession.run_id == row.run_id)
                    )
                ):
                    report.export_status = "PAUSED"
                if row.progress.get("failureCode") in {
                    "TIME_BUDGET_EXCEEDED",
                    "COST_BUDGET_EXCEEDED",
                }:
                    report.export_status = "BUDGET_LIMITED"
                report.error = error or self.failure
                if result and not error and not status:
                    report.usage = result["usage"]
            if (
                result
                and row.run_id
                and row.kind in {"write_file", "apply_patch", "import"}
                and status is None
                and not error
            ):
                run = db.get(Run, row.run_id, with_for_update=True)
                run.disk_bytes = max(0, run.disk_bytes + result.get("diskDelta", 0))
            # Cancellation is acknowledged only after the actual function stopped.
            complete(db, row.id, self.owner, self.version, result, error, status)


def file_job(execution):
    from trustrepro.infrastructure.storage import workspace_lock

    if execution.kind in {"write_file", "apply_patch", "publish", "import"}:
        with workspace_lock(execution.settings.var_root, execution.run_id, execution.checkpoint):
            return execute_file_job(execution)
    if execution.kind == "export":
        with execution.factory() as db:
            identifier = db.get(Job, execution.id).arguments["reportId"]
        with workspace_lock(
            execution.settings.var_root, "export-" + identifier, execution.checkpoint
        ):
            return execute_file_job(execution)
    return execute_file_job(execution)


def execute_file_job(execution):

    with execution.factory() as db:
        job = record(db, Job, execution.id)
        kind, args, run_id = job.kind, job.arguments, job.run_id
    execution.checkpoint()
    root = execution.settings.var_root / "workspaces" / (run_id or "exports")
    root.mkdir(parents=True, exist_ok=True)
    if kind in {"write_file", "apply_patch"}:
        from trustrepro.infrastructure.storage import atomic_write, open_scoped, workspace_relative

        payload = job.handle.get("prepared")
        if payload is None:
            changes = (
                [{"path": args["path"], "content": args["content"]}]
                if kind == "write_file"
                else args["changes"]
            )
            content_by_path, sizes = {}, {}
            for change in changes:
                path = workspace_relative(change["path"])
                if path in content_by_path:
                    before = content_by_path[path]
                else:
                    before = ""
                    try:
                        with open_scoped(root, path, "r") as source:
                            before = source.read()
                    except FileNotFoundError:
                        if kind == "apply_patch":
                            raise
                    sizes[path] = len(before.encode())
                if kind == "apply_patch":
                    if before.count(change["old"]) != 1:
                        raise BusinessError("PATCH_CONFLICT", "补丁原文没有唯一匹配")
                    content_by_path[path] = before.replace(change["old"], change["new"], 1)
                else:
                    content_by_path[path] = change["content"]
            payload = {
                "changes": [{"path": p, "content": v} for p, v in content_by_path.items()],
                "diskDelta": sum(len(v.encode()) - sizes[p] for p, v in content_by_path.items()),
            }
            execution.save_handle({"prepared": payload})
        for change in payload["changes"]:
            atomic_write(root, change["path"], change["content"], execution.checkpoint)
        return {
            "changed": [c["path"] for c in payload["changes"]],
            "diskDelta": payload["diskDelta"],
        }
    if kind == "import":
        from trustrepro.infrastructure.db.models import Artifact
        from trustrepro.infrastructure.storage import atomic_copy, object_path, open_scoped

        with execution.factory() as db:
            artifact = record(db, Artifact, args["artifactId"])
        before = 0
        try:
            with open_scoped(root, args["path"]) as target:
                import os

                before = os.fstat(target.fileno()).st_size
        except FileNotFoundError:
            pass
        with object_path(execution.settings.var_root, artifact.sha256).open("rb") as source:
            atomic_copy(
                root,
                args["path"],
                iter(lambda: source.read(1024 * 1024), b""),
                execution.checkpoint,
            )
        return {
            "path": args["path"],
            "artifactId": artifact.id,
            "diskDelta": artifact.size - before,
        }
    if kind == "publish":
        if job.handle.get("published") is not None:
            return job.handle["published"]
        value = artifacts.publish(
            execution.factory, execution.settings, job.id, args["paths"], execution.checkpoint
        )
        execution.save_handle({"published": value})
        return value
    if kind == "export":
        return artifacts.export(
            execution.factory, execution.settings, args["reportId"], execution.checkpoint
        )
    raise ValueError("未知文件作业")


async def worker(factory, settings, owner, kinds, function, health=None):
    tasks = set()

    def finished(task):
        tasks.discard(task)
        if not task.cancelled() and task.exception():
            log.error("Job execution task stopped", exc_info=task.exception())

    while True:
        try:
            with factory.begin() as db:
                job = claim(db, Job, owner, kinds)
            if health:
                health.beat()
        except SQLAlchemyError:
            if health:
                health.beat(False)
            await asyncio.sleep(3)
            continue
        if job:
            execution = Execution(factory, settings, job, owner)
            task = asyncio.create_task(execution.execute(function))
            tasks.add(task)
            task.add_done_callback(finished)
        await asyncio.sleep(0.2)
