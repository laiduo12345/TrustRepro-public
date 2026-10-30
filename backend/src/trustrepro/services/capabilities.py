"""Few generic client tools; native Codex call IDs identify durable effects."""

import asyncio
import base64
import json
import os
from dataclasses import dataclass

import jsonschema
from sqlalchemy import select

from trustrepro.domain.errors import BusinessError
from trustrepro.domain.runtime import Proposal
from trustrepro.infrastructure.db.models import (
    Artifact,
    Conversation,
    Job,
    Project,
    Report,
    Run,
    new_id,
    now_ms,
)
from trustrepro.infrastructure.storage import (
    list_scoped,
    object_path,
    open_scoped,
    scoped_path,
    workspace_relative,
)
from trustrepro.services import application as app
from trustrepro.services import jobs, research
from trustrepro.services.runtime import authenticate, budget, require_budget


def tool(name, description, properties=None, required=(), schema=None):
    return {
        "type": "function",
        "name": name,
        "description": description,
        "inputSchema": schema
        or {
            "type": "object",
            "properties": properties or {},
            "required": list(required),
            "additionalProperties": False,
        },
    }


S = {"type": "string"}
INDEX = {"type": "integer", "minimum": 0}
A = {"type": "array", "items": S}


@dataclass(frozen=True)
class ImageResult:
    """Image bytes are a typed result, never a JSON text observation."""

    data_url: str


def definitions(kind):
    result = [tool("get_budget", "当前账户剩余额度；不修改授权。")]
    if kind == "research":
        return result + [
            tool(
                "create_project",
                "明确复现意图才建项；普通讨论直接回答。",
                {"title": S, "goal": S, "source": S},
                ("title", "goal"),
            ),
            tool("find_projects", "查找已有项目，只返回入口。", {"query": S}, ("query",)),
            tool(
                "submit_card",
                "提交科学方案与额度；等待用户一次确认。无需源码、依赖清单或审核任务。",
                schema=Proposal.model_json_schema(),
            ),
            tool(
                "read_source",
                "读取公开网页、源码或Notebook；按行继续读取。",
                {
                    "url": S,
                    "start_line": {"type": "integer", "minimum": 1},
                    "line_count": {"type": "integer", "minimum": 1},
                },
                ("url",),
            ),
            tool(
                "read_paper",
                "读取论文PDF页；继续读取可使用nextPage。",
                {
                    "url": S,
                    "page_start": {"type": "integer", "minimum": 1},
                    "page_count": {"type": "integer", "minimum": 1},
                },
                ("url",),
            ),
            tool("search_repositories", "只读查找公开作者仓库。", {"query": S}, ("query",)),
        ]
    if kind == "execution":
        result.append(
            tool(
                "submit_card",
                "只有科学范围确需变更时提交新卡片并暂停当前Run等待批准；普通技术修复不需要。",
                schema=Proposal.model_json_schema(),
            )
        )
    result += [
        tool(
            "list_jobs",
            "按页读取当前Run实际作业与观察；恢复时查询原作业，不重复命令。",
            {"offset": INDEX, "limit": INDEX},
        ),
        tool("list_artifacts", "当前项目已保存的产物。"),
        tool(
            "read_artifact",
            "按范围读取已登记文本产物。",
            {"artifactId": S, "offset": INDEX, "limit": INDEX},
            ("artifactId",),
        ),
        tool(
            "finish_run" if kind == "execution" else "submit_report",
            "提交真实完成声明与完整报告，无摘要章节；章节自由组织。正文块支持text/table/image/file，图表支持id和data.caption，图片与文件data.artifactId使用已登记ID。",
            {
                "result": {
                    "type": "object",
                    "properties": {
                        "title": S,
                        "summary": {
                            "type": "string",
                            "description": "可选聊天卡片短说明，不作为报告正文摘要",
                        },
                        "blocks": {"type": "array", "items": {"type": "object"}},
                    },
                    "required": ["blocks"],
                    "additionalProperties": True,
                },
                "artifactIds": A,
                "status": {"type": "string", "enum": ["COMPLETED", "FAILED"]},
            },
            ("result", "artifactIds"),
        ),
    ]
    result += [
        tool(
            "read_file",
            "读取工作区单个文件；offset/nextOffset为字节游标，无全目录校验。",
            {"path": S, "offset": INDEX, "limit": {"type": "integer", "minimum": 1}},
            ("path",),
        ),
        tool(
            "list_files",
            "列出指定目录的一页文件与子目录。",
            {"path": S, "offset": INDEX, "limit": INDEX},
        ),
        tool(
            "read_image",
            "查看当前Run工作区内的PNG/JPEG/WebP/GIF图片，返回图像内容；path可用/workspace路径。",
            {"path": S},
            ("path",),
        ),
        tool("wait_job", "等待后台作业完成，不消耗模型轮询。", {"jobId": S}, ("jobId",)),
        tool(
            "publish_artifacts",
            "保存选定交付文件，返回artifactId；无需审核或先封存工作区。",
            {"paths": A},
            ("paths",),
        ),
    ]
    if kind == "report":
        return result
    return result + [
        tool(
            "import_artifact",
            "按明确产物ID导入当前项目其他Run的材料到本工作区，不挂载其他工作区。",
            {"artifactId": S, "path": S},
            ("artifactId", "path"),
        ),
        tool(
            "exec",
            "在批准沙箱运行shell命令，工作区与依赖持续保存，允许公网下载和编译。默认等待完成，无需模型轮询；后台执行使用background=true。",
            {"command": S, "cwd": S, "background": {"type": "boolean"}},
            ("command",),
        ),
        tool(
            "write_file", "在当前工作区写入文件。", {"path": S, "content": S}, ("path", "content")
        ),
        tool(
            "apply_patch",
            "按唯一原文替换当前工作区文件；冲突返回观察。",
            {
                "changes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"path": S, "old": S, "new": S},
                        "required": ["path", "old", "new"],
                        "additionalProperties": False,
                    },
                }
            },
            ("changes",),
        ),
        tool("cancel_job", "停止当前Run的一个作业。", {"jobId": S}, ("jobId",)),
    ]


def text_window(path, args, root=None):
    offset, limit = args.get("offset", 0), args.get("limit", 16000)
    source = open_scoped(root, path.relative_to(root).as_posix()) if root else path.open("rb")
    with source as stream:
        stream.seek(offset)
        body = stream.read(limit)
        size = os.fstat(stream.fileno()).st_size
    if b"\0" in body:
        return {"binary": True, "bytes": size}
    return {
        "text": body.decode("utf-8", errors="replace"),
        "nextOffset": offset + len(body),
        "hasMore": offset + len(body) < size,
    }


def image_content(path, root):
    # Bound binary transport memory; oversized figures can be resized in the sandbox.
    limit = 20 * 1024 * 1024
    with open_scoped(root, path.relative_to(root).as_posix()) as stream:
        if os.fstat(stream.fileno()).st_size > limit:
            raise BusinessError(
                "IMAGE_TOO_LARGE", "图片超过20MiB，请在沙箱生成缩小或裁剪版本后读取"
            )
        body = stream.read(limit + 1)
    if len(body) > limit:
        raise BusinessError("IMAGE_TOO_LARGE", "图片超过20MiB，请在沙箱生成缩小或裁剪版本后读取")
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif body.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif body.startswith((b"GIF87a", b"GIF89a")):
        mime = "image/gif"
    elif body.startswith(b"RIFF") and body[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        raise BusinessError(
            "UNSUPPORTED_IMAGE", "请读取PNG、JPEG、WebP或GIF图片；其他格式先在沙箱转换"
        )
    return ImageResult(f"data:{mime};base64,{base64.b64encode(body).decode('ascii')}")


async def invoke(factory, settings, token, call_id, name, args):
    with factory.begin() as db:
        binding = authenticate(db, token, lock=True)
        schema = next(
            (t["inputSchema"] for t in definitions(binding.kind) if t["name"] == name), None
        )
        if schema is None:
            raise BusinessError("SCOPE_DENIED", "当前会话没有此工具权限")
        try:
            jsonschema.validate(args, schema)
        except jsonschema.ValidationError as error:
            path = "/" + "/".join(map(str, error.absolute_path))
            detail = "格式不正确"
            if error.validator == "required":
                detail = "缺少必填字段：" + ", ".join(
                    k for k in error.validator_value if k not in error.instance
                )
            elif error.validator == "type":
                detail = "类型应为：" + str(error.validator_value)
            raise BusinessError("INVALID_TOOL_ARGUMENTS", f"工具参数 {path} {detail}") from None
        scope = binding.kind
        account_id, session_id = binding.account_id, binding.id
        run_id, chat_id, kind = binding.run_id, binding.conversation_id, binding.kind
        key = f"native:{session_id}:{call_id}"
        previous = db.scalar(select(Job).where(Job.operation_key == key))
        if previous:
            if previous.arguments != args or previous.kind != {
                "exec": "command",
                "publish_artifacts": "publish",
                "import_artifact": "import",
            }.get(name, name):
                raise BusinessError("IDEMPOTENCY_CONFLICT", "原生工具标识已绑定其他参数")
            job_id = previous.id
            if previous.status in {"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"}:
                return previous.result or {"error": previous.error, "status": previous.status}
        else:
            job_id = None
        if name == "get_budget":
            return budget(db, binding.account_id, scope)
        require_budget(db, binding.account_id, scope)
        if name == "find_projects":
            return {
                "projects": [
                    {"id": p.id, "title": p.title, "conversationId": p.conversation_id}
                    for p in db.scalars(select(Project))
                    if args["query"].casefold() in (p.title + p.goal).casefold()
                ]
            }
        if name == "list_jobs":
            rows = list(
                db.scalars(
                    select(Job)
                    .where(Job.run_id == run_id)
                    .order_by(Job.created_at)
                    .offset(args.get("offset", 0))
                    .limit(args.get("limit", 50))
                )
            )
            return {
                "jobs": [app.job_value(j) for j in rows],
                "nextOffset": args.get("offset", 0) + len(rows)
                if len(rows) == args.get("limit", 50)
                else None,
            }
        if name == "list_artifacts":
            run = app.record(db, Run, run_id)
            return {
                "artifacts": [
                    app.artifact_value(a)
                    for a in db.scalars(
                        select(Artifact).where(Artifact.project_id == run.project_id)
                    )
                ]
            }
        if name == "read_artifact":
            run = app.record(db, Run, run_id)
            artifact = app.record(db, Artifact, args["artifactId"])
            if artifact.project_id != run.project_id:
                raise BusinessError("SCOPE_DENIED", "产物不属于当前项目")
            path = object_path(settings.var_root, artifact.sha256)
        elif name in {"read_file", "list_files", "read_image"}:
            run = app.record(db, Run, run_id)
            if kind == "execution" and run.status != "RUNNING":
                raise BusinessError("RUN_STOPPED", "当前运行已停止")
            root = settings.var_root / "workspaces" / run_id
            value = workspace_relative(args.get("path", "."))
            path = root if value == "." else scoped_path(root, value)
        else:
            path = None
        if name in {"wait_job", "cancel_job"}:
            target = app.record(db, Job, args["jobId"])
            if target.run_id != run_id or target.account_id != account_id:
                raise BusinessError("SCOPE_DENIED", "作业不属于当前运行")
            if name == "cancel_job":
                target.cancel_requested = True
                return {"jobId": target.id, "status": "CANCELLING"}
            job_id = target.id
        if name in {"create_project", "submit_card", "finish_run", "submit_report"}:
            if previous is None:
                row = Job(
                    id=new_id("job"),
                    operation_key=key,
                    account_id=account_id,
                    session_id=session_id,
                    run_id=run_id,
                    scope=scope,
                    kind=name,
                    arguments=args,
                    status="COMPLETED",
                    owner=binding.owner,
                    owner_version=binding.owner_version,
                    lease_until=binding.lease_until,
                    started_at=now_ms(),
                    finished_at=now_ms(),
                )
                db.add(row)
                chat = app.record(db, Conversation, chat_id)
                if name == "create_project":
                    value = app.create_project(db, chat, **args)
                elif name == "submit_card":
                    value = app.propose(
                        db,
                        chat,
                        args,
                        binding.state.get("inputVersion", 0),
                        pending=kind == "research",
                    )
                    if kind == "execution":
                        app.control_run(db, run_id, "pause")
                        binding.error = "科学范围变更，等待新任务卡批准"
                else:
                    run = db.get(Run, run_id, with_for_update=True)
                    if kind == "execution" and run.status != "RUNNING":
                        raise BusinessError("RUN_STOPPED", "运行已停止")
                    pending = db.scalar(
                        select(Job.id).where(
                            Job.run_id == run_id, Job.status.in_(["QUEUED", "RUNNING", "RETRYING"])
                        )
                    )
                    if pending:
                        raise BusinessError("JOB_PENDING", "还有未结束作业，请先等待或停止")
                    ids = set(args["artifactIds"])
                    available = set(
                        db.scalars(select(Artifact.id).where(Artifact.project_id == run.project_id))
                    )

                    def refs(v):
                        if isinstance(v, dict):
                            for k, child in v.items():
                                if k == "artifactId" and child not in ids:
                                    raise BusinessError(
                                        "INVALID_REFERENCE", "报告引用未列入产物列表"
                                    )
                                refs(child)
                        elif isinstance(v, list):
                            for child in v:
                                refs(child)

                    if ids - available:
                        raise BusinessError("INVALID_REFERENCE", "报告包含其他项目或不存在的产物")
                    json.dumps(args["result"], allow_nan=False)
                    refs(args["result"])
                    versions = list(
                        db.scalars(select(Report.version).where(Report.run_id == run_id))
                    )
                    report = Report(
                        id=new_id("report"),
                        run_id=run.id,
                        project_id=run.project_id,
                        version=max(versions, default=0) + 1,
                        result=args["result"],
                        artifact_ids=sorted(ids),
                    )
                    db.add(report)
                    binding.state = {**binding.state, "completionSubmitted": report.id}
                    from trustrepro.services.lifecycle import emit

                    emit(
                        db,
                        "report:" + report.id,
                        "report_submitted",
                        session_id=binding.id,
                        run_id=run.id,
                        payload={"reportId": report.id},
                    )
                    if kind == "execution":
                        run.status, run.finished_at = args.get("status", "COMPLETED"), now_ms()
                    value = {"reportId": report.id, "published": True}
                    app.append_message(
                        db,
                        chat_id,
                        "assistant",
                        "",
                        blocks=[{"type": "report", "refId": report.id}],
                    )
                row.result = value
                app.event(db, chat_id)
                return value
        if (
            name in {"exec", "write_file", "apply_patch", "publish_artifacts", "import_artifact"}
            and job_id is None
        ):
            job = jobs.enqueue(
                db,
                account_id,
                run_id,
                {
                    "exec": "command",
                    "publish_artifacts": "publish",
                    "import_artifact": "import",
                }.get(name, name),
                args,
                key,
                scope=scope,
                session_id=session_id,
            )
            job_id = job.id
            app.event(db, chat_id, "job", {"jobId": job_id})
    if name in {"read_source", "read_paper", "search_repositories"}:
        return await research.read(factory, settings, binding, call_id, name, args)
    if name in {"read_file", "read_artifact"}:
        return await asyncio.to_thread(
            text_window, path, args, root if name == "read_file" else None
        )
    if name == "list_files":
        return await asyncio.to_thread(
            list_scoped, root, args.get("path", "."), args.get("offset", 0), args.get("limit", 100)
        )
    if name == "read_image":
        return await asyncio.to_thread(image_content, path, root)
    if name == "exec" and args.get("background"):
        return {"jobId": job_id, "status": "QUEUED", "message": "使用wait_job等待完成，无需轮询"}
    value = await jobs.wait(factory, job_id)
    return value
