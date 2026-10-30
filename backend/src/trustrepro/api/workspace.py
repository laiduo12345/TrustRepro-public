"""Typed, paginated read-only desktop endpoints."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from trustrepro.domain.errors import BusinessError
from trustrepro.domain.runtime import Proposal, Resources
from trustrepro.infrastructure.db.models import Job, Report
from trustrepro.services import application as app
from trustrepro.services import workspace as service
from trustrepro.services import workspace_files

router = APIRouter()


def database(request: Request):
    with request.app.state.session_factory() as db:
        yield db


DB = Annotated[object, Depends(database, scope="function")]


class ListQuery(BaseModel):
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=10, ge=1, le=50)
    q: str = Field(default="", max_length=300)
    status: str = Field(default="all", max_length=40)
    order: Literal["asc", "desc"] = "desc"


Params = Annotated[ListQuery, Query()]


class RunQuery(ListQuery):
    projectId: str | None = None
    hasReports: bool = False


class ReportQuery(ListQuery):
    projectId: str | None = None
    runId: str | None = None
    includeHistory: bool = False
    beforeVersion: int | None = Field(default=None, ge=1)


class Allowance(BaseModel):
    model_config = ConfigDict(extra="allow")
    currency: Literal["CNY"]
    confirmed: bool
    limited: bool
    maxCost: float
    maxHours: float
    spentCost: float
    reservedCost: float
    elapsedSeconds: float
    remainingSeconds: float
    resources: Resources | None = None


class ProjectSummary(BaseModel):
    id: str
    title: str
    goal: str
    conversationId: str
    status: str
    createdAt: int | None
    updatedAt: int
    runCount: int
    reportCount: int
    allowance: Allowance | None


class ReportSummary(BaseModel):
    id: str
    projectId: str
    projectTitle: str
    runId: str
    title: str
    version: int
    createdAt: int
    exportStatus: str
    artifactCount: int


class JobSummary(BaseModel):
    id: str
    runId: str
    kind: str
    status: str
    command: str | None = None
    createdAt: int
    startedAt: int | None = None
    finishedAt: int | None = None


class Usage(BaseModel):
    currency: Literal["CNY"] = "CNY"
    spentCost: float
    reservedCost: float
    elapsedSeconds: float


class CurrentJob(BaseModel):
    id: str
    command: str | None
    status: str
    createdAt: int
    finishedAt: int | None


class RunSummary(BaseModel):
    id: str
    projectId: str
    projectTitle: str
    conversationId: str
    planId: str
    title: str
    status: str
    displayStatus: str
    error: str | None
    createdAt: int
    finishedAt: int | None
    automation: dict | None
    jobCount: int
    completedJobs: int
    commandCount: int
    completedCommands: int
    currentJob: CurrentJob | None
    reportCount: int
    latestReport: ReportSummary | None
    usage: Usage


class RunOverview(RunSummary):
    approvedPlan: Proposal
    approvedAt: int


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    pageSize: int
    stats: dict[str, int]


@router.get("/projects", response_model=Page[ProjectSummary])
def projects(db: DB, params: Params):
    return service.projects(db, **params.model_dump())


@router.get("/projects/{identifier}", response_model=ProjectSummary)
def project(identifier: str, db: DB):
    return service.project(db, identifier)


@router.get("/runs", response_model=Page[RunSummary])
def runs(db: DB, params: Annotated[RunQuery, Query()]):
    return service.runs(db, **params.model_dump())


@router.get("/projects/{identifier}/runs", response_model=Page[RunSummary])
def project_runs(identifier: str, db: DB, params: Annotated[RunQuery, Query()]):
    return service.runs(db, **params.model_dump(exclude={"projectId"}), projectId=identifier)


@router.get("/runs/{identifier}/overview", response_model=RunOverview)
def run_overview(identifier: str, db: DB):
    return service.run_overview(db, identifier)


@router.get("/runs/{identifier}/jobs", response_model=Page[JobSummary])
def run_jobs(
    identifier: str,
    db: DB,
    page: int = Query(1, ge=1),
    pageSize: int = Query(10, ge=1, le=50),
    kind: Literal["all", "command", "files"] = "all",
    order: Literal["asc", "desc"] = "desc",
):
    return service.run_jobs(db, identifier, page=page, pageSize=pageSize, kind=kind, order=order)


@router.get("/reports", response_model=Page[ReportSummary])
def reports(db: DB, params: Annotated[ReportQuery, Query()]):
    return service.reports(db, **params.model_dump())


class BatchDownload(BaseModel):
    reportIds: list[str] = Field(min_length=1)


@router.post(
    "/reports/batch-download",
    response_class=StreamingResponse,
    responses={
        200: {"content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}}}
    },
)
def batch_download(body: BatchDownload, request: Request):
    with request.app.state.session_factory() as db:
        values = [
            workspace_files.report_document(db, app.record(db, Report, identifier))
            for identifier in dict.fromkeys(body.reportIds)
        ]
    return StreamingResponse(
        workspace_files.body_archive(values),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="report-bodies.zip"'},
    )


class OutputWindow(BaseModel):
    stdout: str
    stderr: str
    status: str
    exitCode: int | None
    truncated: bool


@router.get("/jobs/{identifier}/output", response_model=OutputWindow)
def job_output(identifier: str, request: Request):
    with request.app.state.session_factory() as db:
        job = app.record(db, Job, identifier)
        if job.kind != "command" or not job.run_id:
            raise BusinessError("INVALID_ARGUMENTS", "该作业没有命令输出")
        value = app.job_value(job)
    return workspace_files.output_window(request.app.state.settings, value)


class RegisteredSource(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    title: str
    kind: str
    url: str


class RegisteredArtifact(BaseModel):
    id: str
    runId: str
    projectId: str
    name: str
    size: int
    sha256: str
    mediaType: str
    sourcePath: str


@router.get("/conversations/{identifier}/sources", response_model=list[RegisteredSource])
def sources(identifier: str, db: DB):
    return service.sources(db, identifier)


@router.get("/projects/{identifier}/artifacts", response_model=list[RegisteredArtifact])
def artifacts(identifier: str, db: DB, runId: str | None = None):
    return service.artifacts(db, identifier, runId)
