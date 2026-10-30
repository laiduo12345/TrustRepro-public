"""Authenticated entry points for the Windows launcher."""

import hmac

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from trustrepro.api.routes import DB
from trustrepro.services import desktop

router = APIRouter()
COOKIE = "trustrepro-desktop"


@router.post("/desktop/connect")
async def connect(request: Request, db: DB):
    token = request.app.state.desktop_token
    form = await request.form()
    supplied = str(form.get("token", ""))
    if not token or not hmac.compare_digest(supplied.encode(), token.encode()):
        raise HTTPException(403, "启动凭据无效，请从桌面应用打开")
    target = "/settings" if form.get("returnTo") == "/settings" else "/"
    if desktop.status(db)["setupRequired"]:
        target = "/settings?setup=1"
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax")
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/api/v1/desktop/status")
def status(db: DB):
    return desktop.status(db)


class StopRequest(BaseModel):
    requestId: str = Field(min_length=1, max_length=100)


@router.post("/api/v1/desktop/stop")
def stop(body: StopRequest, db: DB):
    return desktop.pause(db, "desktop:" + body.requestId)
