"""Idle maintenance after 0009; never starts experiments or billable activity."""

from sqlalchemy import select

from trustrepro.core.config import get_settings
from trustrepro.infrastructure.db.models import AgentSession, Job, Report
from trustrepro.infrastructure.db.session import create_session_factory
from trustrepro.services import artifacts


def refresh(factory, settings):
    with factory() as db:
        active_agents = db.scalar(
            select(AgentSession.id)
            .where(
                AgentSession.status.in_(["RUNNING", "QUEUED", "RECOVERING", "WAITING_JOB"]),
            )
            .limit(1)
        )
        active_jobs = db.scalar(
            select(Job.id).where(Job.status.in_(["RUNNING", "QUEUED"])).limit(1)
        )
        if active_agents or active_jobs:
            raise RuntimeError("Currency maintenance requires an idle platform")
        identifiers = [
            row.id
            for row in db.scalars(select(Report).where(Report.export_status == "READY"))
            if row.publication and row.publication.get("currency") == "CNY"
        ]
    for identifier in identifiers:
        # No Job/ModelRequest/activity is created and no artifact is re-hashed.
        artifacts.export(factory, settings, identifier, lambda: None)
    return len(identifiers)


if __name__ == "__main__":
    settings = get_settings()
    engine, factory = create_session_factory(settings)
    try:
        print("Updated saved report archives:", refresh(factory, settings))
    finally:
        engine.dispose()
