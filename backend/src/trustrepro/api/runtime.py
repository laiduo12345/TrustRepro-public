from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from trustrepro.services.model_gateway import forward

router = APIRouter(prefix="/internal")


@router.post("/model/{path:path}")
async def model(path: str, request: Request):
    stream, status, headers, identifier = await forward(
        request.app.state.session_factory,
        request.app.state.settings,
        request.headers.get("authorization", "").removeprefix("Bearer "),
        path,
        await request.body(),
        dict(request.headers),
    )
    return StreamingResponse(
        stream, status_code=status, headers={**headers, "X-TrustRepro-Request": identifier}
    )
