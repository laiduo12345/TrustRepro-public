"""Read existing output and serialize existing report versions without starting work."""

import copy
import os
from tempfile import SpooledTemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile

from trustrepro.domain.errors import BusinessError
from trustrepro.infrastructure.storage import open_scoped
from trustrepro.infrastructure.text import visible_nul
from trustrepro.services import artifacts
from trustrepro.services.application import json_bytes
from trustrepro.services.report_document import markdown


def report_document(db, report):
    return (
        copy.deepcopy(report.publication)
        if report.publication is not None
        else artifacts.publication(db, report)
    )


def report_body(value, kind):
    if kind == "json":
        return json_bytes(value)
    if kind == "markdown":
        return markdown(value)
    raise BusinessError("INVALID_FORMAT", "导出格式无效")


def body_archive(documents):
    stream = SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    try:
        with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
            for value in documents:
                archive.writestr(value["id"] + "/report.json", report_body(value, "json"))
                archive.writestr(value["id"] + "/report.md", report_body(value, "markdown"))
        stream.seek(0)
        yield from iter(lambda: stream.read(65536), b"")
    finally:
        stream.close()


def output_window(settings, job):
    result = job.get("result") or {}
    result = result if isinstance(result, dict) else {}
    value = {"status": job["status"], "exitCode": result.get("exitCode"), "truncated": False}
    for name in ("stdout", "stderr"):
        try:
            with open_scoped(
                settings.var_root / "workspaces" / job["runId"], f".trustrepro/{job['id']}/{name}"
            ) as stream:
                size = os.fstat(stream.fileno()).st_size
                stream.seek(max(0, size - 65536))
                text = stream.read(65536).decode("utf-8", errors="replace")
                value["truncated"] |= size > 65536
        except FileNotFoundError:
            saved = result.get(name, "")
            text = (
                saved.get("text", "")
                if isinstance(saved, dict)
                else saved
                if isinstance(saved, str)
                else ""
            )
            value["truncated"] |= bool(isinstance(saved, dict) and saved.get("hasMore"))
        value[name] = visible_nul(text)
    return value
