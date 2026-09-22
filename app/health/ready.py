"""Readiness: the instance can serve traffic (startup complete, DB and storage reachable)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.infrastructure.database.session import check_database
from app.schemas.common import HealthResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


def _run_checks(request: Request) -> tuple[bool, dict[str, str]]:
    checks: dict[str, str] = {}
    healthy = True
    readiness = request.app.state.readiness
    if not readiness.ready:
        checks["startup"] = "shutting_down" if readiness.shutting_down else "starting"
        healthy = False
    else:
        checks["startup"] = "ok"
    try:
        check_database(request.app.state.engine)
        checks["database"] = "ok"
    except Exception as exc:
        logger.warning("readiness: database check failed", extra={"error": type(exc).__name__})
        checks["database"] = "unavailable"
        healthy = False
    try:
        request.app.state.storage.check()
        checks["storage"] = "ok"
    except Exception as exc:
        logger.warning("readiness: storage check failed", extra={"error": type(exc).__name__})
        checks["storage"] = "unavailable"
        healthy = False
    return healthy, checks


@router.get(
    "/health/ready",
    response_model=HealthResponse,
    summary="Readiness probe",
    responses={503: {"model": HealthResponse, "description": "Not ready"}},
)
def ready(request: Request) -> JSONResponse:
    healthy, checks = _run_checks(request)
    body = HealthResponse(status="ok" if healthy else "unavailable", checks=checks).model_dump()
    return JSONResponse(status_code=200 if healthy else 503, content=body, headers={"Cache-Control": "no-store"})
