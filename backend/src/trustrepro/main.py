import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from trustrepro.api.desktop import router as desktop_router
from trustrepro.api.desktop_host import install as install_desktop
from trustrepro.api.routes import router
from trustrepro.api.runtime import router as runtime_router
from trustrepro.api.workspace import router as workspace_router
from trustrepro.core.config import Settings, get_settings
from trustrepro.domain.errors import BusinessError
from trustrepro.infrastructure.db.session import create_session_factory


def create_app(settings: Settings | None = None):
    config = settings or get_settings()
    engine, factory = create_session_factory(config)

    @asynccontextmanager
    async def lifespan(app):
        config.var_root.mkdir(parents=True, exist_ok=True)
        from trustrepro.services import preferences

        app.state.secret_cipher = preferences.cipher(config)
        encrypted = (
            app.state.secret_cipher.encrypt(
                config.model_api_key.get_secret_value().encode()
            ).decode()
            if config.model_api_key.get_secret_value()
            else ""
        )
        with factory.begin() as db:
            preferences.bootstrap(db, config, encrypted)
        yield
        engine.dispose()

    app = FastAPI(title="TrustRepro API", version="1.0.0", lifespan=lifespan)
    app.state.settings, app.state.session_factory = config, factory
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.allowed_origins,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "Last-Event-ID"],
    )

    @app.middleware("http")
    async def protect_settings(request, call_next):
        if request.method not in {"GET", "OPTIONS"} and request.url.path.startswith(
            "/api/v1/settings"
        ):
            origin = request.headers.get("origin")
            same_origin = origin == str(request.base_url).rstrip("/")
            if origin and origin not in config.allowed_origins and not (
                config.desktop_token_file and same_origin
            ):
                return JSONResponse(status_code=403, content={"message": "不允许的请求来源"})
        return await call_next(request)

    @app.exception_handler(BusinessError)
    async def business_error(request, error):
        status = (
            404
            if error.code == "NOT_FOUND"
            else 409
            if error.code in {"VERSION_CONFLICT", "IDEMPOTENCY_CONFLICT"}
            else 422
        )
        return JSONResponse(
            status_code=status, content={"code": error.code, "message": error.message}
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        return JSONResponse(
            status_code=422,
            content={
                "code": "INVALID_ARGUMENTS",
                "message": "请求字段不符合要求",
                "details": [{"loc": e["loc"], "type": e["type"]} for e in error.errors()],
            },
        )

    @app.exception_handler(SQLAlchemyError)
    async def db_error(request, error):
        logging.getLogger(__name__).exception("Database error")
        return JSONResponse(
            status_code=503, content={"code": "DATABASE_UNAVAILABLE", "message": "数据库暂时不可用"}
        )

    app.include_router(router)
    app.include_router(workspace_router, prefix="/api/v1")
    app.include_router(runtime_router)
    if config.desktop_token_file:
        app.include_router(desktop_router)
    install_desktop(app, config)
    return app


app = create_app()
