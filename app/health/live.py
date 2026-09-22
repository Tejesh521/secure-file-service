"""Liveness: the process is up and the event loop responds. No dependency checks."""

from fastapi import APIRouter

from app.schemas.common import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health/live", response_model=HealthResponse, summary="Liveness probe")
async def live() -> HealthResponse:
    return HealthResponse(status="ok")
