"""Publish selected files once; exports reuse immutable content objects."""

import hashlib
import mimetypes
import os
from zipfile import ZIP_DEFLATED, ZipFile

from sqlalchemy import select

from trustrepro.domain.errors import BusinessError
from trustrepro.infrastructure.db.models import (
    AgentSession,
    Artifact,
    Job,
    ModelRequest,
    Plan,
    Report,
    Run,
    Source,
)
from trustrepro.infrastructure.storage import ingest, object_path, scoped_path
from trustrepro.services.application import artifact_value, json_bytes, record
from trustrepro.services.jobs import enqueue
from trustrepro.services.report_document import archive_path, markdown


def publish(factory, settings, job_id, paths, checkpoint):
    with factory() as db:
        job = record(db, Job, job_id)
        run = record(db, Run, job.run_id)
        run_id, project_id = run.id, run.project_id
    values = []
    for index, name in enumerate(paths):
        checkpoint()
        target = scoped_path(settings.var_root / "workspaces" / run_id, name)
        if not target.is_file() or target.is_symlink():
            raise BusinessError("INVALID_PATH", "请选择当前工作区内的普通文件")
        sha, size = ingest(
            settings.var_root,
            target,
            f"{job_id}-{index}",
            checkpoint,
            workspace=settings.var_root / "workspaces" / run_id,
            relative=name,
        )
        identifier = "artifact-" + hashlib.sha256(f"{job_id}:{name}".encode()).hexdigest()[:32]
        values.append(
            {
                "id": identifier,
                "run_id": run_id,
                "project_id": project_id,
                "name": name,
                "source_path": name,
                "sha256": sha,
                "size": size,
                "media_type": mimetypes.guess_type(name)[0] or "application/octet-stream",
            }
        )
    return values


def save_published(db, values):
    result = []
    for value in values:
        row = db.get(Artifact, value["id"])
        if row is None:
            row = Artifact(**value)
            db.add(row)
        result.append(artifact_value(row))
    return {"artifacts": result}


def schedule_exports(db):
    for report in db.scalars(
        select(Report).where(Report.export_status == "WAITING").with_for_update(skip_locked=True)
    ):
        run = record(db, Run, report.run_id)
        if any(
            a.state.get("exportPaused")
            for a in db.scalars(select(AgentSession).where(AgentSession.run_id == run.id))
        ):
            continue
        active = db.scalar(
            select(AgentSession.id).where(
                AgentSession.run_id == run.id,
                AgentSession.status.in_(["RUNNING", "QUEUED", "RECOVERING"]),
            )
        )
        unresolved = db.scalar(
            select(ModelRequest.id).where(
                ModelRequest.account_id == run.account_id,
                ModelRequest.usage_scope == "reproduction",
                ModelRequest.status == "REQUESTED",
            )
        )
        if active or unresolved:
            continue
        if report.publication is None:
            report.publication = publication(db, report)
        try:
            job = enqueue(
                db,
                run.account_id,
                run.id,
                "export",
                {"reportId": report.id},
                f"export:{report.id}",
                scope="report",
            )
            report.export_job_id, report.export_status = job.id, "QUEUED"
        except BusinessError as error:
            report.export_status = (
                "BUDGET_LIMITED"
                if error.code in {"TIME_BUDGET_EXCEEDED", "COST_BUDGET_EXCEEDED"}
                else "FAILED"
            )
            report.error = error.message


def resume_budget_exports(db, run_id, statuses=("BUDGET_LIMITED",)):
    from trustrepro.services.runtime import require_budget

    if not run_id:
        return
    run = db.get(Run, run_id)
    try:
        require_budget(db, run.account_id, "report")
    except BusinessError:
        return
    for report in db.scalars(
        select(Report).where(Report.run_id == run_id, Report.export_status.in_(statuses))
    ):
        job = db.get(Job, report.export_job_id) if report.export_job_id else None
        if job and job.status in {"QUEUED", "RUNNING", "RETRYING"}:
            continue
        if job:
            job.status, job.cancel_requested, job.retry_at = "QUEUED", False, 0
            job.progress = {k: v for k, v in job.progress.items() if k != "failureCode"}
            report.export_status = "QUEUED"
        else:
            report.export_status = "WAITING"
        report.error = None


def publication(db, report):
    run = record(db, Run, report.run_id)
    calls = list(
        db.scalars(
            select(ModelRequest)
            .where(ModelRequest.account_id == run.account_id)
            .order_by(ModelRequest.created_at)
        )
    )
    usage = [
        {
            "id": c.id,
            "currency": "CNY",
            "purpose": c.purpose,
            "usageScope": c.usage_scope,
            "model": c.connection.get("model"),
            "pricing": c.pricing,
            "status": c.status,
            "usage": c.usage,
            "estimatedCost": str(c.actual_cost or 0),
            "reservedCost": str(c.reserved_cost),
            "createdAt": c.created_at,
            "finishedAt": c.finished_at,
        }
        for c in calls
    ]
    artifacts = [artifact_value(record(db, Artifact, i)) for i in report.artifact_ids]
    jobs = list(db.scalars(select(Job).where(Job.run_id == run.id, Job.kind == "command")))
    from trustrepro.services.runtime import budget

    return {
        "id": report.id,
        "currency": "CNY",
        "version": report.version,
        "runId": run.id,
        "projectId": run.project_id,
        "createdAt": report.created_at,
        "result": report.result,
        "artifacts": artifacts,
        "approvedPlan": record(db, Plan, run.plan_id).content,
        "sources": [
            record(db, Source, i).content
            for i in record(db, Plan, run.plan_id).content.get("sources", [])
        ],
        "usage": usage,
        "budget": budget(db, run.account_id, "report"),
        "commands": [
            {
                "id": j.id,
                "command": j.arguments,
                "status": j.status,
                "result": j.result,
                "error": j.error,
                "startedAt": j.started_at,
                "finishedAt": j.finished_at,
            }
            for j in jobs
        ],
    }


def export(factory, settings, identifier, checkpoint):
    with factory.begin() as db:
        row = db.get(Report, identifier, with_for_update=True)
        if row.publication is None:
            row.publication = publication(db, row)
        value = row.publication
    root = settings.var_root / "exports" / identifier
    root.mkdir(parents=True, exist_ok=True)
    bodies = {"report.json": json_bytes(value), "report.md": markdown(value)}
    temporary = root / "artifacts.zip.part"
    with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
        for name, body in bodies.items():
            archive.writestr(name, body)
        archive.writestr("manifest.json", json_bytes(value["artifacts"]))
        for artifact in value["artifacts"]:
            checkpoint()
            path = object_path(settings.var_root, artifact["sha256"])
            name = archive_path(artifact)
            with path.open("rb") as source, archive.open(name, "w", force_zip64=True) as target:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    checkpoint()
                    target.write(chunk)
    for name, body in bodies.items():
        (root / (name + ".part")).write_bytes(body)
        os.replace(root / (name + ".part"), root / name)
    os.replace(temporary, root / "artifacts.zip")
    return {"reportId": identifier, "usage": value["usage"], "files": [*bodies, "artifacts.zip"]}
