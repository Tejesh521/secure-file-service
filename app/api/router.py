"""Top-level router: health, metrics and versioned API."""

from fastapi import APIRouter, Request, Response

from app.api.v1.router import router as v1_router
from app.health import live, ready

router = APIRouter()
router.include_router(live.router)
router.include_router(ready.router)
router.include_router(v1_router)


@router.get("/metrics", include_in_schema=False)
def metrics(request: Request) -> Response:
    payload, content_type = request.app.state.metrics.render()
    return Response(content=payload, media_type=content_type)
