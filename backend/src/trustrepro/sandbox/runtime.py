"""The only component that controls Docker. PostgreSQL owns every operation."""

import json
import os
import threading
import time

import docker
from docker.errors import DockerException, NotFound
from docker.types import Mount
from requests import RequestException
from sqlalchemy import select

from trustrepro.domain.errors import BusinessError
from trustrepro.infrastructure.db.models import Job, Plan, Run, now_ms
from trustrepro.infrastructure.disk_usage import DiskUsageCache
from trustrepro.infrastructure.storage import (
    atomic_copy,
    open_scoped,
    scoped_path,
    workspace_relative,
)
from trustrepro.infrastructure.text import visible_nul
from trustrepro.services.application import json_bytes, record


class Sandbox:
    def __init__(self, factory, settings):
        self.factory, self.settings = factory, settings
        self.engine = docker.from_env()
        self.locks = {}
        self.usage = DiskUsageCache()
        self.lock = threading.Lock()

    def run_lock(self, identifier):
        with self.lock:
            return self.locks.setdefault(identifier, threading.Lock())

    def container(self, execution, job):
        identifier = job.run_id or job.id
        with self.run_lock(identifier):
            name = "trustrepro-runtime-" + identifier
            with self.factory() as db:
                if job.run_id:
                    run = record(db, Run, job.run_id)
                    plan = record(db, Plan, run.plan_id).content
                    resources = plan["allocation"]["resources"]
                    relative = "workspaces/" + run.id
                else:
                    plan, resources = {}, {"cpus": 2, "memoryGb": 4, "diskGb": 10}
                    relative = "research/" + job.arguments["conversationId"]
            workspace = self.settings.var_root / relative
            workspace.mkdir(parents=True, exist_ok=True)
            for part in (".home", ".tmp", ".trustrepro"):
                (workspace / part).mkdir(exist_ok=True)
            control = self.settings.var_root / "control" / identifier
            control.mkdir(parents=True, exist_ok=True)
            for filename, body in (
                ("plan.json", plan),
                ("parameters.json", plan.get("parameters", {})),
            ):
                if not (control / filename).exists():
                    atomic_copy(
                        control, filename, [json_bytes(body)], execution.checkpoint, mode=0o444
                    )
            for directory in (
                workspace,
                *(workspace / p for p in (".home", ".tmp", ".trustrepro")),
            ):
                os.chown(directory, 10001, 10001)
            try:
                container = self.engine.containers.get(name)
            except NotFound:
                mounts = [
                    Mount(
                        "/workspace", self.settings.runtime_volume, type="volume", subpath=relative
                    ),
                    Mount(
                        "/run/trustrepro",
                        self.settings.runtime_volume,
                        type="volume",
                        subpath="control/" + identifier,
                        read_only=True,
                    ),
                ]
                env = {
                    "HOME": "/workspace/.home",
                    "TMPDIR": "/workspace/.tmp",
                    "UV_CACHE_DIR": "/workspace/.cache/uv",
                    "PIP_CACHE_DIR": "/workspace/.cache/pip",
                    "PYTHONUNBUFFERED": "1",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PATH": "/workspace/.venv/bin:/usr/local/bin:/usr/bin:/bin",
                    "TRUSTREPRO_PARAMETERS_FILE": "/run/trustrepro/parameters.json",
                    "TRUSTREPRO_PLAN_FILE": "/run/trustrepro/plan.json",
                }
                if job.run_id:
                    env.update(
                        HTTP_PROXY=self.settings.egress_proxy,
                        HTTPS_PROXY=self.settings.egress_proxy,
                        http_proxy=self.settings.egress_proxy,
                        https_proxy=self.settings.egress_proxy,
                    )
                container = self.engine.containers.create(
                    self.settings.sandbox_image,
                    ["sleep", "infinity"],
                    name=name,
                    user="10001:10001",
                    working_dir="/workspace",
                    mounts=mounts,
                    network=self.settings.sandbox_network if job.run_id else "none",
                    read_only=True,
                    cap_drop=["ALL"],
                    security_opt=["no-new-privileges:true"],
                    mem_limit=int(resources["memoryGb"] * 1024**3),
                    nano_cpus=int(resources["cpus"] * 1_000_000_000),
                    pids_limit=512,
                    tmpfs={"/tmp": "rw,noexec,nosuid,size=64m,mode=1777"},
                    environment=env,
                    labels={"trustrepro.managed": "true", "trustrepro.run": identifier},
                )
            container.reload()
            if container.status in {"created", "exited"}:
                if job.handle.get("execId"):
                    raise BusinessError("INTERRUPTED", "沙箱已停止，原命令不会重复启动")
                container.start()
            if job.run_id:
                with self.factory.begin() as db:
                    current = record(db, Job, job.id)
                    from trustrepro.services.runtime import fence

                    fence(current, execution.owner, execution.version)
                    row = record(db, Run, job.run_id)
                    row.container_id = container.id
            return container, workspace, resources

    def execute(self, execution):
        with self.factory() as db:
            job = record(db, Job, execution.id)
            args, kind = job.arguments, job.kind
        execution.checkpoint()
        container, workspace, resources = self.container(execution, job)
        cwd = workspace_relative(args.get("cwd", "."))
        if cwd != ".":
            scoped_path(workspace, cwd)
        command = (
            args["command"]
            if kind == "command"
            else (
                "python /opt/trustrepro/parse_paper.py "
                + args["file"]
                + " "
                + args["output"]
                + " "
                + str(args["page_start"])
                + " "
                + str(args["page_count"])
            )
        )
        state_dir = workspace / ".trustrepro" / job.id
        handle = job.handle
        if handle.get("execId"):
            exec_id = handle["execId"]
        else:
            exec_id = self.engine.api.exec_create(
                container.id,
                [
                    "python",
                    "/opt/trustrepro/command.py",
                    job.id,
                    command,
                    "/workspace" + ("/" + cwd if cwd != "." else ""),
                ],
                user="10001:10001",
                workdir="/workspace",
            )["Id"]
            execution.save_handle({"execId": exec_id, "containerId": container.id})
        status = self.inspect(exec_id, execution)
        if not status["Running"] and not (state_dir / "started").exists():
            if status.get("ExitCode") not in {0, None}:
                raise BusinessError("INTERRUPTED", "命令启动已失败，保留原执行句柄")
            execution.checkpoint()
            try:
                self.engine.api.exec_start(exec_id, detach=True)
            except (DockerException, RequestException):
                # The daemon can have started it before a lost start receipt.
                if (
                    not self.inspect(exec_id, execution)["Running"]
                    and not (state_dir / "started").exists()
                ):
                    raise BusinessError("INTERRUPTED", "命令启动回执不明，保留原句柄，不重复启动")
        while True:
            status = self.inspect(exec_id, execution)
            if not status["Running"]:
                break
            if execution.cancelled.is_set():
                try:
                    container.exec_run(
                        ["python", "/opt/trustrepro/command.py", job.id, "--cancel", "."]
                    )
                    deadline = time.monotonic() + 3
                    while (
                        self.inspect(exec_id, execution)["Running"] and time.monotonic() < deadline
                    ):
                        time.sleep(0.1)
                    if self.inspect(exec_id, execution)["Running"]:
                        container.kill()
                except (DockerException, RequestException):
                    # Cancellation remains pending until the original handle confirms it stopped.
                    execution.failure = execution.failure or "等待容器服务恢复以确认停止"
            time.sleep(0.2)
        stdout = self.window(workspace, f".trustrepro/{job.id}/stdout")
        stderr = self.window(workspace, f".trustrepro/{job.id}/stderr")
        result = {
            "exitCode": status["ExitCode"],
            "stdout": stdout,
            "stderr": stderr,
            "stdoutPath": f".trustrepro/{job.id}/stdout",
            "stderrPath": f".trustrepro/{job.id}/stderr",
        }
        try:
            with open_scoped(workspace, f".trustrepro/{job.id}/result.json", "r") as receipt:
                result["commandSeconds"] = json.load(receipt)["seconds"]
        except FileNotFoundError:
            pass
        if kind == "parse_pdf":
            if result["exitCode"] == 0:
                with open_scoped(workspace, args["output"], "r") as paper:
                    result["paper"] = json.load(paper)
            container.stop(timeout=2)
        return visible_nul(result)

    def inspect(self, exec_id, execution=None):
        while True:
            if execution and (execution.ownership_lost or now_ms() >= execution.ownership_deadline):
                raise BusinessError("OWNER_EXPIRED", "所有权已失效，保留原命令供接管")
            try:
                return self.engine.api.exec_inspect(exec_id)
            except NotFound as error:
                raise BusinessError(
                    "INTERRUPTED", "原命令句柄已不可查询，不重复启动，已有文件保留"
                ) from error
            except (DockerException, RequestException):
                if execution and execution.cancelled.is_set():
                    # Keep waiting for Docker acknowledgement; do not release resources early.
                    execution.failure = execution.failure or "等待容器服务恢复以确认停止"
                time.sleep(1)

    @staticmethod
    def window(root, relative):
        try:
            with open_scoped(root, relative) as stream:
                body = stream.read(16000)
                size = os.fstat(stream.fileno()).st_size
        except FileNotFoundError:
            return {"text": "", "hasMore": False, "nextOffset": 0}
        return {
            "text": visible_nul(body.decode("utf-8", errors="replace")),
            "hasMore": size > len(body),
            "nextOffset": len(body),
        }

    def sample_active(self):
        with self.factory() as db:
            runs = [
                (r.id, db.get(Plan, r.plan_id).content["allocation"]["resources"]["diskGb"])
                for r in db.scalars(select(Run).where(Run.status == "RUNNING"))
            ]
        for identifier, cap in runs:
            sample = self.usage.read(
                identifier, (self.settings.var_root / "workspaces" / identifier,)
            )
            if not sample.sampled_at:
                continue
            with self.factory.begin() as db:
                run = db.get(Run, identifier, with_for_update=True)
                if run.status != "RUNNING":
                    continue
                run.disk_bytes, run.disk_sample_at = sample.total, now_ms()
                if sample.total > cap * 1024**3:
                    from trustrepro.infrastructure.db.models import AgentSession
                    from trustrepro.services.lifecycle import emit

                    agent = db.scalar(
                        select(AgentSession).where(
                            AgentSession.run_id == run.id,
                            AgentSession.kind == "execution",
                            AgentSession.desired_state == "ACTIVE",
                        )
                    )
                    if agent:
                        emit(
                            db,
                            f"disk-limit:{run.id}:{agent.owner_version}",
                            "budget_limit",
                            session_id=agent.id,
                            payload={"reason": "运行磁盘额度已用尽"},
                        )

    def stop_terminal(self):
        with self.factory() as db:
            runs = [
                (r.id, r.container_id)
                for r in db.scalars(select_runs())
                if r.container_id
                and not db.scalar(
                    select(Job.id).where(Job.run_id == r.id, Job.status.in_(["RUNNING", "QUEUED"]))
                )
            ]
        for identifier, container_id in runs:
            try:
                container = self.engine.containers.get(container_id)
                if container.status == "running":
                    container.stop(timeout=2)
            except NotFound:
                pass


def select_runs():
    from sqlalchemy import select

    from trustrepro.infrastructure.db.models import AgentSession

    stopped_agent = (
        select(AgentSession.id)
        .where(
            AgentSession.run_id == Run.id,
            AgentSession.kind == "execution",
            AgentSession.status.in_(["BLOCKED", "PAUSED", "CANCELLED", "WAITING_USER"]),
        )
        .exists()
    )
    return select(Run).where(
        Run.status.in_(["COMPLETED", "FAILED", "PAUSED", "CANCELLED"]) | stopped_agent
    )
