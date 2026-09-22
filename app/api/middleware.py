"""ASGI middleware: request correlation, access logging, metrics, body size limits.

Implemented as pure ASGI callables (not ``BaseHTTPMiddleware``) so that streaming
responses such as file downloads are not buffered.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import request_id_ctx, user_id_ctx
from app.core.telemetry import Metrics

logger = logging.getLogger("app.access")

REQUEST_ID_HEADER = b"x-request-id"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex}"


class RequestBodyTooLarge(HTTPException):
    """Raised from ``receive`` when a streamed body exceeds its limit.

    Subclassing ``HTTPException`` matters: FastAPI re-raises HTTP exceptions that
    surface while it reads the body, but converts any other exception into a
    generic 400. This way the client sees a precise 413.
    """

    def __init__(self, limit: int) -> None:
        super().__init__(status_code=413, detail=f"Request body exceeds {limit} bytes.")
        self.limit = limit


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return str(value.decode("latin-1"))
    return None


class RequestIdMiddleware:
    """Preserve a valid client ``X-Request-ID`` or mint one; echo it on the response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = _header(scope, REQUEST_ID_HEADER)
        request_id = incoming if incoming and _VALID_REQUEST_ID.match(incoming) else new_request_id()
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_ctx.set(request_id)
        user_token = user_id_ctx.set(None)

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((REQUEST_ID_HEADER, request_id.encode("ascii")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            request_id_ctx.reset(token)
            user_id_ctx.reset(user_token)


class AccessLogMiddleware:
    """One structured log line and one set of metrics per request."""

    def __init__(self, app: ASGIApp, metrics: Metrics) -> None:
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = scope["method"]
        start = time.perf_counter()
        status_holder = {"status": 500}
        self.metrics.http_requests_in_progress.labels(method=method).inc()

        async def send_capture(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_capture)
        finally:
            duration = time.perf_counter() - start
            route = _route_template(scope)
            status = status_holder["status"]
            self.metrics.http_requests_in_progress.labels(method=method).dec()
            self.metrics.http_requests_total.labels(method=method, route=route, status_code=str(status)).inc()
            self.metrics.http_request_duration_seconds.labels(method=method, route=route).observe(duration)
            if route not in {"/health/live", "/health/ready", "/metrics"} or status >= 400:
                logger.info(
                    "request completed",
                    extra={
                        "method": method,
                        "route": route,
                        "path": scope.get("path"),
                        "status_code": status,
                        "duration_ms": round(duration * 1000, 2),
                        "client_ip": _client_ip(scope),
                        "user_agent": _header(scope, b"user-agent"),
                        "user_id": scope.get("state", {}).get("user_id"),
                    },
                )


_STATIC_ROUTES = {"/openapi.json", "/docs"}


def _route_template(scope: Scope) -> str:
    """Return the matched route template (``/v1/files/{file_id}``) for metrics labels.

    Nested routers only expose the innermost template in ``scope["route"]``, so we
    rebuild the full template by substituting path parameter values back into the
    concrete path. Unmatched paths collapse to one label to bound cardinality.
    """
    path = str(scope.get("path", ""))
    if scope.get("route") is None:
        return path if path in _STATIC_ROUTES else "unmatched"
    params: dict[str, Any] = scope.get("path_params") or {}
    if not params:
        return path
    segments = path.split("/")
    for name, value in params.items():
        literal = str(value)
        for index, segment in enumerate(segments):
            if segment == literal:
                segments[index] = "{" + name + "}"
                break
    return "/".join(segments)


def _client_ip(scope: Scope) -> str | None:
    client = scope.get("client")
    return str(client[0]) if client else None


class BodySizeLimitMiddleware:
    """Reject oversized bodies early using Content-Length, and enforce while streaming.

    ``POST`` requests to one of ``upload_paths`` may carry up to ``upload_limit``
    bytes; every other request is capped at ``default_limit``.
    """

    def __init__(
        self, app: ASGIApp, default_limit: int, upload_paths: set[str] | None = None, upload_limit: int | None = None
    ) -> None:
        self.app = app
        self.default_limit = default_limit
        self.upload_paths = {p.rstrip("/") for p in (upload_paths or set())}
        self.upload_limit = upload_limit if upload_limit is not None else default_limit

    def _limit_for(self, method: str, path: str) -> int:
        if method == "POST" and path.rstrip("/") in self.upload_paths:
            return self.upload_limit
        return self.default_limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self._limit_for(scope.get("method", ""), scope.get("path", ""))
        declared = _header(scope, b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await _send_json(send, 413, "PAYLOAD_TOO_LARGE", f"Request body exceeds {limit} bytes.", scope)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise RequestBodyTooLarge(limit)
            return message

        await self.app(scope, limited_receive, send)


async def _send_json(send: Send, status: int, code: str, message: str, scope: Scope) -> None:
    import json

    request_id = scope.get("state", {}).get("request_id")
    body = json.dumps({"error": {"code": code, "message": message, "details": [], "request_id": request_id}}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


SendCallable = Callable[[Message], Awaitable[None]]
AnyDict = dict[str, Any]


class ProbeFriendlyTrustedHostMiddleware(TrustedHostMiddleware):
    """``TrustedHostMiddleware`` that lets platform health probes through.

    Orchestrators (App Platform, Kubernetes) probe ``/health/*`` with the pod IP as the
    ``Host`` header, never the public domain, so a strict host allow-list would fail every
    readiness check and the deploy would never go live. Health endpoints carry no user data,
    so exempting them costs nothing; every other route keeps the strict check.
    """

    def __init__(
        self, app: ASGIApp, allowed_hosts: list[str], exempt_prefixes: tuple[str, ...] = ("/health/",)
    ) -> None:
        super().__init__(app, allowed_hosts=allowed_hosts)
        self._exempt_prefixes = exempt_prefixes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"].startswith(self._exempt_prefixes):
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)
