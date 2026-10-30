"""Local desktop lifecycle; use the same controls as the workspace."""

from sqlalchemy import select

from trustrepro.domain.errors import BusinessError
from trustrepro.infrastructure.db.models import AgentSession, Job, ModelConnection
from trustrepro.services import application, preferences

ACTIVE_JOBS = ("QUEUED", "RUNNING", "RETRYING")
ACTIVE_AGENTS = ("QUEUED", "RUNNING", "WAITING_JOB", "RECOVERING")


def status(db):
    configured = True
    try:
        bindings = preferences.default_bindings(db)
        configured = bool(bindings)
        for choice in bindings.values():
            row = db.get(ModelConnection, choice["connectionId"])
            if choice.get("auth") != "none" and not row.secret:
                configured = False
    except BusinessError:
        configured = False
    agents = list(db.scalars(select(AgentSession).where(AgentSession.status.in_(ACTIVE_AGENTS))))
    jobs = list(db.scalars(select(Job).where(Job.status.in_(ACTIVE_JOBS))))
    return {
        "setupRequired": not configured,
        "busy": bool(agents or jobs),
        "activeAgents": len(agents),
        "activeJobs": len(jobs),
    }


def pause(db, request_id):
    # Include pending exports, even after the execution agent has finished.
    ids = set(
        db.scalars(
            select(AgentSession.conversation_id).where(AgentSession.status.in_(ACTIVE_AGENTS))
        )
    )
    for job in db.scalars(select(Job).where(Job.status.in_(ACTIVE_JOBS))):
        if job.session_id and (agent := db.get(AgentSession, job.session_id)):
            ids.add(agent.conversation_id)
    for identifier in sorted(ids):
        application.control_conversation(db, identifier, "pause", request_id)
    return status(db)
