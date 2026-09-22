"""Application factory and ASGI entry point."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from app import __version__
from app.api.errors import register_exception_handlers
from app.api.middleware import AccessLogMiddleware, BodySizeLimitMiddleware, RequestIdMiddleware
from app.api.router import router
from app.core.config import Settings, get_settings
from app.core.lifecycle import build_lifespan
from app.core.telemetry import Metrics

DESCRIPTION = """
Private file storage with cryptographically signed, time-limited download links.

* **Owner endpoints** (`/v1/files/*`) require an `X-API-Key` header.
* **Download endpoint** (`/v1/download/{file_id}`) is public and authenticated solely by the
  HMAC signature embedded in the URL.
* Every non-2xx response uses the same `{"error": {...}}` envelope and carries `X-Request-ID`.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    metrics = Metrics()
    app = FastAPI(
        title="Secure File Service",
        version=__version__,
        description=DESCRIPTION,
        lifespan=build_lifespan(settings),
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )
    app.state.metrics = metrics

    register_exception_handlers(app)
    app.include_router(router)
    _use_error_envelope_in_openapi(app)

    # Middleware order: last added runs first (outermost).
    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-API-Key", "Idempotency-Key", "X-Request-ID"],
            expose_headers=["X-Request-ID", "Location"],
        )
    if settings.trusted_host_list != ["*"]:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_host_list)
    app.add_middleware(
        BodySizeLimitMiddleware,
        default_limit=settings.max_request_body_bytes,
        upload_paths={"/v1/files"},
        # Multipart framing adds a little overhead beyond the file itself.
        upload_limit=settings.max_upload_bytes + 64 * 1024,
    )
    app.add_middleware(AccessLogMiddleware, metrics=metrics)
    app.add_middleware(RequestIdMiddleware)
    return app


def _use_error_envelope_in_openapi(app: FastAPI) -> None:
    """Make the generated spec truthful about validation errors.

    FastAPI documents 422 responses with its default ``HTTPValidationError`` schema,
    but the exception handlers translate every validation failure into the shared
    ``ErrorResponse`` envelope. The contract tests validate real responses against
    the spec, so the spec must describe what is actually sent.
    """
    original = app.openapi

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = original()
        error_ref = {"$ref": "#/components/schemas/ErrorResponse"}
        for operations in schema.get("paths", {}).values():
            for operation in operations.values():
                response = operation.get("responses", {}).get("422")
                if response is None:
                    continue
                response["description"] = "Validation error"
                response["content"] = {"application/json": {"schema": error_ref}}
        components = schema.get("components", {}).get("schemas", {})
        components.pop("HTTPValidationError", None)
        components.pop("ValidationError", None)
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]


app = create_app()
