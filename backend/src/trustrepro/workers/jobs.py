import asyncio
import logging
import uuid

from trustrepro.core.config import get_settings
from trustrepro.infrastructure.db.session import create_session_factory
from trustrepro.services.executors import file_job, worker


async def main():
    settings = get_settings()
    engine, factory = create_session_factory(settings)
    from trustrepro.infrastructure.health import ServiceHealth

    health = ServiceHealth(settings, "job-worker")
    try:
        await worker(
            factory,
            settings,
            uuid.uuid4().hex,
            {"write_file", "apply_patch", "publish", "export", "import"},
            file_job,
            health,
        )
    finally:
        health.close()
        engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
