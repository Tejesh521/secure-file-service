"""JSON log formatting, request-id correlation, route templates."""

from __future__ import annotations

import json
import logging

from app.api.middleware import _VALID_REQUEST_ID, _route_template, new_request_id
from app.core.logging import JsonFormatter, request_id_ctx, user_id_ctx


def test_json_formatter_includes_context_and_extras() -> None:
    formatter = JsonFormatter("svc", "test")
    record = logging.LogRecord("app.x", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    record.file_id = "f-1"
    token = request_id_ctx.set("req_abc")
    user_token = user_id_ctx.set("alice")
    try:
        payload = json.loads(formatter.format(record))
    finally:
        request_id_ctx.reset(token)
        user_id_ctx.reset(user_token)
    assert payload["message"] == "hello world"
    assert payload["service"] == "svc" and payload["environment"] == "test"
    assert payload["request_id"] == "req_abc" and payload["user_id"] == "alice"
    assert payload["file_id"] == "f-1"
    assert payload["level"] == "INFO" and payload["timestamp"].endswith("+00:00")
    assert "args" not in payload and "msg" not in payload


def test_json_formatter_renders_exceptions() -> None:
    formatter = JsonFormatter("svc", "test")
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord("app.x", logging.ERROR, __file__, 1, "failed", None, sys.exc_info())
    payload = json.loads(formatter.format(record))
    assert "ValueError: boom" in payload["exception"]


def test_request_id_validation() -> None:
    assert new_request_id().startswith("req_") and _VALID_REQUEST_ID.match(new_request_id())
    assert _VALID_REQUEST_ID.match("abc-123_XYZ.9:0")
    assert not _VALID_REQUEST_ID.match("short")
    assert not _VALID_REQUEST_ID.match("x" * 129)
    assert not _VALID_REQUEST_ID.match("has space in it")
    assert not _VALID_REQUEST_ID.match("inject\nheader")


def test_route_template_reconstruction() -> None:
    scope = {"path": "/v1/files/abc/links", "route": object(), "path_params": {"file_id": "abc"}}
    assert _route_template(scope) == "/v1/files/{file_id}/links"
    assert _route_template({"path": "/v1/files", "route": object(), "path_params": {}}) == "/v1/files"
    assert _route_template({"path": "/nope", "route": None}) == "unmatched"
    assert _route_template({"path": "/openapi.json", "route": None}) == "/openapi.json"
