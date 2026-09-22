"""Map every failure to the single error envelope.

Clients see one shape for validation errors, domain errors and unexpected
failures. Internal details never leak: a 500 carries only the request id, which
is enough to find the full stack trace in the logs.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.middleware import RequestBodyTooLarge
from app.domain.files import exceptions as domain

logger = logging.getLogger(__name__)

# Domain exception -> HTTP status. Codes come from the exception classes.
DOMAIN_STATUS: dict[type[domain.DomainError], int] = {
    domain.FileNotFound: 404,
    domain.FileUnavailable: 410,
    domain.EmptyUpload: 422,
    domain.UploadTooLarge: 413,
    domain.InvalidTTL: 422,
    domain.LinkSignatureInvalid: 403,
    domain.LinkExpired: 410,
    domain.IdempotencyConflict: 409,
    domain.StorageInconsistent: 500,
    domain.InvalidStateTransition: 409,
}

_HTTP_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    503: "SERVICE_UNAVAILABLE",
}


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return str(value) if value else None


def error_response(
    request: Request,
    status: int,
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "details": details or [], "request_id": _request_id(request)}}
    return JSONResponse(status_code=status, content=body, headers=headers)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(domain.DomainError)
    async def _domain(request: Request, exc: domain.DomainError) -> JSONResponse:
        status = DOMAIN_STATUS.get(type(exc), 400)
        if status >= 500:
            logger.error("domain failure", extra={"code": exc.code, **exc.context})
            return error_response(request, status, exc.code, "Internal server error.")
        return error_response(request, status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = []
        for err in exc.errors():
            loc = [str(p) for p in err.get("loc", []) if p not in ("body",)]
            details.append({"field": ".".join(loc) or None, "reason": err.get("msg", "invalid value")})
        return error_response(request, 422, "VALIDATION_ERROR", "Request validation failed.", details)

    @app.exception_handler(RequestBodyTooLarge)
    async def _too_large(request: Request, exc: RequestBodyTooLarge) -> JSONResponse:
        return error_response(request, 413, "PAYLOAD_TOO_LARGE", f"Request body exceeds {exc.limit} bytes.")

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
        detail: Any = exc.detail
        if isinstance(detail, dict) and "code" in detail:
            code = str(detail["code"])
            message = str(detail.get("message", ""))
        else:
            message = str(detail) if detail else code.replace("_", " ").capitalize() + "."
        headers = dict(exc.headers) if isinstance(exc, HTTPException) and exc.headers else None
        return error_response(request, exc.status_code, code, message, headers=headers)

    @app.exception_handler(OperationalError)
    async def _db_unavailable(request: Request, exc: OperationalError) -> JSONResponse:
        logger.error("database unavailable", exc_info=exc)
        return error_response(request, 503, "SERVICE_UNAVAILABLE", "A dependency is unavailable. Retry later.")

    @app.exception_handler(SQLAlchemyError)
    async def _db_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.error("database error", exc_info=exc)
        return error_response(request, 500, "INTERNAL_ERROR", "Internal server error.")

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled exception")
        # This handler runs in Starlette's outermost ServerErrorMiddleware, outside
        # RequestIdMiddleware, so the correlation header must be added here.
        request_id = _request_id(request)
        headers = {"X-Request-ID": request_id} if request_id else None
        return error_response(request, 500, "INTERNAL_ERROR", "Internal server error.", headers=headers)
