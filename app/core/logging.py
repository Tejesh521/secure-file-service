"""Structured logging with request correlation.

Every log line is a single JSON object so that platform log pipelines can index
fields without parsing free text. The request id is carried in a ``ContextVar``
that the request-id middleware sets, so any log emitted while handling a request
is automatically correlated.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

request_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
user_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar("user_id", default=None)

_RESERVED = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "message",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str, environment: str) -> None:
        super().__init__()
        self._service = service
        self._environment = environment

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "service": self._service,
            "environment": self._environment,
            "message": record.getMessage(),
        }
        request_id = request_id_ctx.get()
        if request_id:
            payload["request_id"] = request_id
        user_id = user_id_ctx.get()
        if user_id:
            payload["user_id"] = user_id
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        request_id = request_id_ctx.get() or "-"
        base = f"{record.levelname:<7} {record.name} [{request_id}] {record.getMessage()}"
        extras = {k: v for k, v in record.__dict__.items() if k not in _RESERVED and not k.startswith("_")}
        if extras:
            base += " " + " ".join(f"{k}={v}" for k, v in extras.items())
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def configure_logging(level: str, fmt: str, service: str, environment: str) -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    formatter: logging.Formatter
    formatter = JsonFormatter(service, environment) if fmt == "json" else ConsoleFormatter()
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(level)
    # Uvicorn's own access log duplicates our structured access log; silence it.
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("uvicorn.error").propagate = True
    logging.getLogger("uvicorn.error").handlers.clear()
