"""Authentication, the error envelope and request correlation."""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from tests.conftest import upload


def assert_envelope(body: dict, code: str) -> None:  # type: ignore[type-arg]
    assert set(body) == {"error"}
    err = body["error"]
    assert set(err) == {"code", "message", "details", "request_id"}
    assert err["code"] == code
    assert err["request_id"].startswith("req_") or len(err["request_id"]) >= 8


def test_missing_api_key(client: TestClient) -> None:
    r = client.get("/v1/files")
    assert r.status_code == 401
    assert_envelope(r.json(), "UNAUTHORIZED")
    assert r.headers["www-authenticate"] == "ApiKey"


def test_wrong_api_key(client: TestClient) -> None:
    r = client.get("/v1/files", headers={"X-API-Key": "nope"})
    assert r.status_code == 401
    assert_envelope(r.json(), "UNAUTHORIZED")


def test_unknown_route_uses_envelope(client: TestClient) -> None:
    r = client.get("/v1/nothing")
    assert r.status_code == 404
    assert_envelope(r.json(), "NOT_FOUND")


def test_method_not_allowed_uses_envelope(client: TestClient, alice: dict[str, str]) -> None:
    r = client.put("/v1/files", headers=alice)
    assert r.status_code == 405
    assert_envelope(r.json(), "METHOD_NOT_ALLOWED")


def test_validation_error_lists_fields(client: TestClient, alice: dict[str, str]) -> None:
    r = client.get("/v1/files?limit=0&offset=-1", headers=alice)
    assert r.status_code == 422
    body = r.json()
    assert_envelope(body, "VALIDATION_ERROR")
    fields = {d["field"] for d in body["error"]["details"]}
    assert fields == {"query.limit", "query.offset"}
    assert all(d["reason"] for d in body["error"]["details"])


def test_malformed_json_body(client: TestClient, alice: dict[str, str]) -> None:
    file_id = upload(client, alice).json()["id"]
    r = client.post(
        f"/v1/files/{file_id}/links", headers={**alice, "Content-Type": "application/json"}, content="{not json"
    )
    assert r.status_code == 422
    assert_envelope(r.json(), "VALIDATION_ERROR")


def test_request_id_generated_and_echoed(client: TestClient) -> None:
    r = client.get("/health/live")
    rid = r.headers["x-request-id"]
    assert rid.startswith("req_") and len(rid) == 36


def test_valid_client_request_id_preserved(client: TestClient) -> None:
    r = client.get("/v1/files", headers={"X-Request-ID": "trace-abc-12345"})
    assert r.headers["x-request-id"] == "trace-abc-12345"
    assert r.json()["error"]["request_id"] == "trace-abc-12345"


def test_invalid_client_request_id_replaced(client: TestClient) -> None:
    r = client.get("/health/live", headers={"X-Request-ID": "bad id with spaces"})
    assert r.headers["x-request-id"] != "bad id with spaces"
    assert r.headers["x-request-id"].startswith("req_")


def test_unexpected_exception_is_sanitised(app: FastAPI, client: TestClient) -> None:
    @app.get("/__boom")
    def boom() -> None:
        raise RuntimeError("secret internal detail")

    with TestClient(app, raise_server_exceptions=False) as raw:
        r = raw.get("/__boom")
    assert r.status_code == 500
    body = r.json()
    assert_envelope(body, "INTERNAL_ERROR")
    assert "secret" not in json.dumps(body)
    assert body["error"]["request_id"] == r.headers["x-request-id"]


def test_database_outage_is_503(app: FastAPI, client: TestClient) -> None:
    @app.get("/__db-down")
    def db_down() -> None:
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    r = client.get("/__db-down")
    assert r.status_code == 503
    assert_envelope(r.json(), "SERVICE_UNAVAILABLE")


def test_oversized_json_body_rejected_by_content_length(client: TestClient, alice: dict[str, str]) -> None:
    payload = b"{" + b" " * (17 * 1024) + b"}"
    r = client.post("/v1/files/x/links", headers={**alice, "Content-Type": "application/json"}, content=payload)
    assert r.status_code == 413
    assert_envelope(r.json(), "PAYLOAD_TOO_LARGE")


def test_oversized_streamed_body_rejected(client: TestClient, alice: dict[str, str]) -> None:
    def chunks():  # type: ignore[no-untyped-def]
        for _ in range(20):
            yield b"x" * 1024

    r = client.post(
        "/v1/files/x/links",
        headers={**alice, "Content-Type": "application/json", "Transfer-Encoding": "chunked"},
        content=chunks(),
    )
    assert r.status_code == 413
    assert_envelope(r.json(), "PAYLOAD_TOO_LARGE")


def test_openapi_and_docs_available(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    assert spec["info"]["title"] == "Secure File Service"
    assert "/v1/files/{file_id}/links" in spec["paths"]
    assert client.get("/docs").status_code == 200
