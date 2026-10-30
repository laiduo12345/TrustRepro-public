"""Serve the installed UI and protect its loopback HTTP endpoint."""

import hmac

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from trustrepro.api.desktop import COOKIE


def install(app, config):
    app.state.desktop_token = (
        config.desktop_token_file.read_text().strip() if config.desktop_token_file else ""
    )
    if config.desktop_token_file and len(app.state.desktop_token) < 32:
        raise ValueError("Desktop token is missing or invalid")

    @app.middleware("http")
    async def desktop_access(request: Request, call_next):
        token = app.state.desktop_token
        path = request.url.path
        if (
            token
            and path not in {"/api/v1/health", "/desktop/connect"}
            and not path.startswith("/internal/model/")
        ):
            supplied = request.cookies.get(COOKIE, "")
            bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
            if not any(
                hmac.compare_digest(value.encode(), token.encode()) for value in (supplied, bearer)
            ):
                return JSONResponse(status_code=403, content={"message": "请从桌面应用打开"})
            origin = request.headers.get("origin")
            if request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
                if origin != str(request.base_url).rstrip("/"):
                    return JSONResponse(status_code=403, content={"message": "不允许的请求来源"})
        response = await call_next(request)
        if token:
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Cache-Control"] = "no-store"
        return response

    if config.frontend_root:
        root = config.frontend_root.resolve()
        if not (root / "index.html").is_file():
            raise ValueError("Installed frontend is missing")

        @app.get("/{path:path}", include_in_schema=False)
        def frontend(path: str):
            if path.startswith(("api/", "internal/", "desktop/")):
                raise HTTPException(404)
            file = (root / path).resolve()
            if not file.is_relative_to(root):
                raise HTTPException(404)
            if file.is_file():
                return FileResponse(file)
            if path.startswith("assets/") or "." in file.name:
                raise HTTPException(404)
            return FileResponse(root / "index.html")
