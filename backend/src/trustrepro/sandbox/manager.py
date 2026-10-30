"""Private Docker executor; all durable state lives in PostgreSQL."""

import asyncio
import os
import signal
import uuid
from contextlib import asynccontextmanager

from docker.errors import DockerException
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from requests.exceptions import RequestException
from sqlalchemy.exc import SQLAlchemyError

from trustrepro.core.config import get_settings
from trustrepro.infrastructure.db.session import create_session_factory
from trustrepro.infrastructure.health import ServiceHealth
from trustrepro.sandbox.runtime import Sandbox
from trustrepro.services.executors import worker


@asynccontextmanager
async def lifespan(app):
    settings = get_settings()
    engine, factory = create_session_factory(settings)
    sandbox = Sandbox(factory, settings)
    health = ServiceHealth(settings, "sandbox-manager")
    reaper_health = ServiceHealth(settings, "sandbox-reaper")
    task = asyncio.create_task(
        worker(
            factory, settings, uuid.uuid4().hex, {"command", "parse_pdf"}, sandbox.execute, health
        )
    )

    async def reap():
        while True:
            try:
                await asyncio.to_thread(sandbox.sample_active)
                await asyncio.to_thread(sandbox.stop_terminal)
                reaper_health.beat()
            except (SQLAlchemyError, DockerException, RequestException):
                reaper_health.beat(False)
            await asyncio.sleep(2)

    reaper = asyncio.create_task(reap())
    app.state.tasks = [task, reaper]
    app.state.health = [health, reaper_health]

    async def supervise():
        await asyncio.wait(app.state.tasks, return_when=asyncio.FIRST_COMPLETED)
        health.close()
        reaper_health.close()
        # An unexpectedly ended loop must restart with its durable leases, not serve a zombie API.
        os.kill(os.getpid(), signal.SIGTERM)

    supervisor = asyncio.create_task(supervise())
    yield
    supervisor.cancel()
    health.close()
    reaper_health.close()
    task.cancel()
    reaper.cancel()
    await asyncio.gather(task, reaper, supervisor, return_exceptions=True)
    sandbox.usage.close()
    engine.dispose()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health():
    healthy = all(not task.done() for task in app.state.tasks) and all(
        value.ready() for value in app.state.health
    )
    return JSONResponse(
        {"status": "ok" if healthy else "unavailable"}, status_code=200 if healthy else 503
    )
