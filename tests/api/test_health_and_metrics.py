from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import make_settings, upload


def test_liveness_has_no_dependencies(client: TestClient) -> None:
    r = client.get("/health/live")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "checks": {}}


def test_readiness_reports_each_check(client: TestClient) -> None:
    r = client.get("/health/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "checks": {"startup": "ok", "database": "ok", "storage": "ok"}}
    assert r.headers["cache-control"] == "no-store"


def test_readiness_fails_when_database_unreachable(app: FastAPI, client: TestClient) -> None:
    real_engine = app.state.engine
    broken = MagicMock()
    broken.connect.side_effect = ConnectionError("refused")
    app.state.engine = broken
    try:
        r = client.get("/health/ready")
    finally:
        app.state.engine = real_engine
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "unavailable"
    assert body["checks"]["database"] == "unavailable" and body["checks"]["storage"] == "ok"


def test_readiness_fails_during_shutdown(app: FastAPI, client: TestClient) -> None:
    state = app.state.readiness
    state.ready, state.shutting_down = False, True
    try:
        r = client.get("/health/ready")
    finally:
        state.ready, state.shutting_down = True, False
    assert r.status_code == 503
    assert r.json()["checks"]["startup"] == "shutting_down"


def test_metrics_exposed_with_bounded_route_labels(client: TestClient, alice: dict[str, str]) -> None:
    file_id = upload(client, alice).json()["id"]
    client.get(f"/v1/files/{file_id}", headers=alice)
    client.get("/does/not/exist")
    body = client.get("/metrics").text
    assert 'http_requests_total{method="POST",route="/v1/files",status_code="201"}' in body
    assert 'route="/v1/files/{file_id}"' in body
    assert f'route="/v1/files/{file_id}"' not in body  # no per-id cardinality
    assert 'route="unmatched"' in body
    assert "files_uploaded_total 1.0" in body
    assert "http_request_duration_seconds_bucket" in body
    assert "db_query_duration_seconds_count" in body


def test_probes_bypass_trusted_host_check_but_api_routes_do_not(tmp_path: Path) -> None:
    """App Platform / Kubernetes probe with the pod IP as Host, never the public domain."""
    from app.main import create_app

    strict = create_app(make_settings(tmp_path, trusted_hosts="files.test"))
    with TestClient(strict, base_url="http://10.0.0.7:8080") as probe:
        assert probe.get("/health/live").status_code == 200
        assert probe.get("/v1/files").status_code == 400
    with TestClient(strict, base_url="http://files.test") as public:
        assert public.get("/health/live").status_code == 200
        assert public.get("/v1/files").status_code == 401
