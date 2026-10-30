"""Supervise durable transitions without running model or scientific work."""

import asyncio
import logging

from sqlalchemy.exc import SQLAlchemyError

from trustrepro.core.config import get_settings
from trustrepro.infrastructure.db.session import create_session_factory
from trustrepro.infrastructure.health import ServiceHealth
from trustrepro.services.lifecycle import tick
from trustrepro.services.model_gateway import recover_requests


async def main():
    settings = get_settings()
    engine, factory = create_session_factory(settings)
    health = ServiceHealth(settings, "progression")
    try:
        while True:
            try:
                await asyncio.to_thread(recover_requests, factory, settings)
                with factory.begin() as db:
                    tick(db)
                health.beat()
                await asyncio.sleep(0.5)
            except SQLAlchemyError:
                health.beat(False)
                await asyncio.sleep(3)
    finally:
        health.close()
        engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
