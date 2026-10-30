"""Thin local product API. Long effects are durable jobs, never request work."""

import asyncio
import json
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Header, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from trustrepro.api.conversation_types import ConversationEvent, ConversationSnapshot
from trustrepro.domain.errors import BusinessError
from trustrepro.infrastructure.db.models import (
    Artifact,
    Conversation,
    Event,
    Job,
    Project,
    Report,
    Run,
    Source,
    new_id,
)
from trustrepro.infrastructure.storage import atomic_copy, object_path
from trustrepro.services import application as service
from trustrepro.services import preferences

router = APIRouter(prefix="/api/v1")


def database(request: Request):
    with request.app.state.session_factory.begin() as db:
        yield db


DB = Annotated[object, Depends(database, scope="function")]


class NewConversation(BaseModel):
    clientConversationId: str
    models: preferences.ModelSelections | None = None


class NewMessage(BaseModel):
    clientMessageId: str
    content: str


class CardEdit(BaseModel):
    expectedVersion: int
    expectedRevision: int
    clientRequestId: str
    values: dict = Field(default_factory=dict)
    allocation: dict | None = None


class CardConfirm(BaseModel):
    expectedVersion: int
    expectedRevision: int


class Control(BaseModel):
    action: str
    clientRequestId: str | None = None


class Retry(BaseModel):
    clientRequestId: str
    maxHours: float | None = Field(default=None, gt=0)
    regenerate: bool = False


@router.get("/health")
def health(db: DB):
    from sqlalchemy import text

    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.get("/conversations")
def conversations(db: DB):
    return [
        {
            "id": c.id,
            "title": c.title,
            "createdAt": c.created_at,
            "projectId": (p.id if (p := service.project_for(db, c.id)) else None),
        }
        for c in db.scalars(select(Conversation).order_by(Conversation.created_at.desc()))
    ]


@router.post("/conversations", response_model=ConversationSnapshot)
def new_conversation(body: NewConversation, request: Request, db: DB):
    return service.snapshot(
        db,
        service.create_conversation(
            db,
            body.clientConversationId,
            request.app.state.settings,
            body.models.model_dump() if body.models else None,
        ).id,
    )


@router.get("/conversations/{identifier}", response_model=ConversationSnapshot)
def conversation(identifier: str, db: DB):
    return service.snapshot(db, identifier)


@router.post("/conversations/{identifier}/messages", response_model=ConversationSnapshot)
def message(identifier: str, body: NewMessage, db: DB):
    service.submit_message(db, identifier, body.clientMessageId, body.content)
    return service.snapshot(db, identifier)


@router.put("/conversations/{identifier}/cards/{card_id}")
def edit(identifier: str, card_id: str, body: CardEdit, db: DB):
    service.edit_card(
        db,
        service.record(db, Conversation, identifier),
        card_id,
        body.expectedVersion,
        body.values,
        body.allocation,
        body.expectedRevision,
        body.clientRequestId,
    )
    return service.snapshot(db, identifier)


@router.post("/conversations/{identifier}/cards/{card_id}/confirm")
def confirm(identifier: str, card_id: str, body: CardConfirm, db: DB):
    service.confirm(
        db,
        service.record(db, Conversation, identifier),
        card_id,
        body.expectedVersion,
        body.expectedRevision,
    )
    return service.snapshot(db, identifier)


@router.get("/workspace")
def workspace(db: DB):
    return {
        "projects": [
            {
                "id": p.id,
                "title": p.title,
                "goal": p.goal,
                "conversationId": p.conversation_id,
                "allowance": service.project_budget(db, p),
            }
            for p in db.scalars(select(Project))
        ],
        "runs": [service.run_value(db, r) for r in db.scalars(select(Run))],
        "reports": [service.report_value(db, r) for r in db.scalars(select(Report))],
    }


@router.get("/runs/{identifier}")
def get_run(identifier: str, db: DB):
    return service.run_value(db, service.record(db, Run, identifier))


@router.post("/runs/{identifier}/actions")
def run_control(identifier: str, body: Control, db: DB):
    service.control_run(db, identifier, body.action, body.clientRequestId)
    return service.run_value(db, service.record(db, Run, identifier))


@router.get("/jobs/{identifier}")
def get_job(identifier: str, db: DB):
    return service.job_value(service.record(db, Job, identifier))


@router.post("/jobs/{identifier}/cancel")
def cancel_job(identifier: str, db: DB):
    job = service.record(db, Job, identifier)
    job.cancel_requested = True
    return service.job_value(job)


@router.get("/reports/{identifier}")
def report(identifier: str, db: DB):
    return service.report_value(db, service.record(db, Report, identifier))


@router.post("/reports/{identifier}/retry")
def retry_report(identifier: str, body: Retry, db: DB):
    service.report_retry(
        db,
        identifier,
        body.clientRequestId,
        body.maxHours,
        body.regenerate,
    )
    return service.report_value(db, service.record(db, Report, identifier))


@router.post("/runs/{identifier}/report")
def generate_report(identifier: str, body: Retry, db: DB):
    return service.request_report(db, identifier, body.clientRequestId, body.maxHours)


@router.post("/conversations/{identifier}/actions")
def conversation_control(identifier: str, body: Control, db: DB):
    service.control_conversation(db, identifier, body.action, body.clientRequestId)
    return service.snapshot(db, identifier)


@router.get("/reports/{identifier}/download")
def download(identifier: str, request: Request, db: DB, format: str = "json"):
    report = service.record(db, Report, identifier)
    if format in {"json", "markdown"}:
        from trustrepro.services.workspace_files import report_body, report_document

        extension = "json" if format == "json" else "md"
        return Response(
            report_body(report_document(db, report), format),
            media_type="application/json" if format == "json" else "text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{identifier}.{extension}"'},
        )
    if report.export_status != "READY":
        raise BusinessError("EXPORT_NOT_READY", "产物包尚未生成")
    names = {"json": "report.json", "markdown": "report.md", "zip": "artifacts.zip"}
    if format not in names:
        raise BusinessError("INVALID_FORMAT", "导出格式无效")
    path = request.app.state.settings.var_root / "exports" / identifier / names[format]
    return FileResponse(path, filename=identifier + "." + names[format].split(".")[-1])


@router.get("/artifacts/{identifier}")
def artifact(identifier: str, request: Request, db: DB, preview: bool = False):
    row = service.record(db, Artifact, identifier)
    path = object_path(request.app.state.settings.var_root, row.sha256)
    inline = preview and row.media_type in {"image/png", "image/jpeg", "image/gif", "image/webp"}
    return FileResponse(
        path,
        media_type=row.media_type,
        filename=row.name.rsplit("/", 1)[-1],
        content_disposition_type="inline" if inline else "attachment",
    )


@router.post("/conversations/{identifier}/attachments")
def upload(identifier: str, request: Request, file: Annotated[UploadFile, File()]):
    import hashlib

    with request.app.state.session_factory() as db:
        chat = service.record(db, Conversation, identifier)
    if file.file.read(5) != b"%PDF-":
        raise BusinessError("INVALID_PDF", "请上传 PDF")
    file.file.seek(0)
    sha = hashlib.file_digest(file.file, "sha256").hexdigest()
    file.file.seek(0)
    root = request.app.state.settings.var_root / "research" / chat.id
    root.mkdir(parents=True, exist_ok=True)
    path = root / (sha + ".body")
    if not path.exists():
        atomic_copy(root, path.name, iter(lambda: file.file.read(1024 * 1024), b""), lambda: None)
    source = Source(
        id="source-" + hashlib.sha256((chat.id + sha).encode()).hexdigest()[:40],
        conversation_id=chat.id,
        content={},
    )
    source.content = {
        "id": source.id,
        "url": "upload:" + sha,
        "title": file.filename or "论文.pdf",
        "kind": "paper",
    }
    with request.app.state.session_factory.begin() as db:
        if db.get(Source, source.id) is None:
            db.add(source)
        service.submit_message(
            db, chat.id, new_id("upload"), "已上传论文：" + source.content["url"]
        )
    return source.content


@router.get(
    "/conversations/{identifier}/events",
    response_model=ConversationEvent,
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "SSE; id is the durable cursor; data contains ConversationEvent JSON.",
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string"},
                    "x-event-data-schema": {"$ref": "#/components/schemas/ConversationEvent"},
                }
            },
        }
    },
)
async def events(
    identifier: str,
    request: Request,
    last_event_id: Annotated[str | None, Header()] = None,
    after: int = 0,
):
    cursor = int(last_event_id or after)

    def read(position):
        with request.app.state.session_factory() as db:
            service.record(db, Conversation, identifier)
            return [
                {"id": e.id, "kind": e.kind, "payload": e.payload}
                for e in db.scalars(
                    select(Event)
                    .where(Event.conversation_id == identifier, Event.id > position)
                    .order_by(Event.id)
                    .limit(200)
                )
            ]

    await asyncio.to_thread(read, cursor)

    async def stream():
        nonlocal cursor
        while not await request.is_disconnected():
            rows = await asyncio.to_thread(read, cursor)
            for row in rows:
                cursor = row["id"]
                yield f"id: {cursor}\ndata: {json.dumps(row, ensure_ascii=False)}\n\n"
            if not rows:
                yield ": keepalive\n\n"
            await asyncio.sleep(0.7)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class QuestionAnswer(BaseModel):
    clientRequestId: str
    answers: dict[str, dict[str, list[str]]]


@router.post("/questions/{identifier}/answer")
def answer_question(identifier: str, body: QuestionAnswer, db: DB):
    from trustrepro.services.lifecycle import answer

    answer(db, identifier, body.clientRequestId, body.answers)
    return {"accepted": True}


class BudgetEdit(BaseModel):
    currency: Literal["CNY"]
    clientRequestId: str
    maxCost: float = Field(
        ge=0, le=670000, allow_inf_nan=False, description="人民币累计费用上限（元）"
    )
    maxHours: float = Field(gt=0, le=720, allow_inf_nan=False)
    scope: Literal["all", "report"] = "all"


@router.put("/projects/{identifier}/budget")
def project_budget(identifier: str, body: BudgetEdit, db: DB):
    return service.adjust_budget(
        db, identifier, body.clientRequestId, body.maxCost, body.maxHours, body.scope
    )


@router.get("/settings")
def get_preferences(db: DB):
    return preferences.snapshot(db)


@router.put("/settings")
def update_preferences(body: preferences.DefaultsInput, db: DB):
    return preferences.save_defaults(db, body)


@router.put("/settings/connections/{identifier}")
def update_connection(identifier: str, body: preferences.ConnectionInput, request: Request):
    encrypted = None
    if body.apiKey is not None:
        key = body.apiKey.get_secret_value()
        encrypted = request.app.state.secret_cipher.encrypt(key.encode()).decode() if key else ""
    with request.app.state.session_factory.begin() as db:
        return preferences.save_connection(db, identifier, body, encrypted)


class DeleteConnectionInput(BaseModel):
    expectedVersion: int = Field(ge=1)


@router.delete("/settings/connections/{identifier}")
def delete_connection(identifier: str, body: DeleteConnectionInput, db: DB):
    return preferences.delete_connection(db, identifier, body.expectedVersion)


@router.post("/settings/models/discover")
async def discover_models(body: preferences.ModelDiscoveryInput, request: Request):
    from trustrepro.services.model_catalog import discover

    return await discover(
        request.app.state.session_factory,
        request.app.state.secret_cipher,
        body,
        request.app.state.settings,
    )


class ProbeInput(BaseModel):
    clientRequestId: str
    model: str = Field(min_length=1, max_length=200)


@router.post("/settings/connections/{identifier}/test")
async def test_connection(identifier: str, body: ProbeInput, request: Request):
    from trustrepro.services.connection_probe import probe

    return await probe(
        request.app.state.session_factory,
        request.app.state.secret_cipher,
        identifier,
        body.clientRequestId,
        body.model,
        request.app.state.settings,
    )


class ModelBindingInput(preferences.ModelSelection):
    role: preferences.ModelRole


@router.put("/conversations/{identifier}/model")
def switch_model(identifier: str, body: ModelBindingInput, db: DB):
    preferences.switch_model(db, identifier, body.role, body.connectionId, body.model)
    return service.snapshot(db, identifier)
