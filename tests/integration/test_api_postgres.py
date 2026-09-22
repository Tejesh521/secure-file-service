"""Full HTTP flow against PostgreSQL, including concurrent idempotent uploads."""

from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.conftest import ALICE_KEY, signed_path, upload

pytestmark = pytest.mark.integration
ALICE = {"X-API-Key": ALICE_KEY}


def test_end_to_end_flow(pg_client: TestClient) -> None:
    created = upload(pg_client, ALICE, b"postgres bytes", "pg.txt")
    assert created.status_code == 201
    file_id = created.json()["id"]

    link = pg_client.post(f"/v1/files/{file_id}/links", headers=ALICE, json={"ttl_seconds": 60})
    assert link.status_code == 201
    download = pg_client.get(signed_path(link.json()["url"]))
    assert download.status_code == 200 and download.content == b"postgres bytes"

    audit = pg_client.get(f"/v1/files/{file_id}/audit", headers=ALICE).json()
    assert [e["event_type"] for e in audit["items"]] == ["file.downloaded", "link.generated", "file.uploaded"]

    assert pg_client.delete(f"/v1/files/{file_id}", headers=ALICE).status_code == 204
    assert pg_client.get(signed_path(link.json()["url"])).status_code == 410
    assert pg_client.get("/health/ready").json()["checks"]["database"] == "ok"


def test_concurrent_uploads_with_same_idempotency_key_create_one_file(pg_client: TestClient, pg_engine) -> None:  # type: ignore[no-untyped-def]
    def attempt(_: int) -> int:
        r = pg_client.post(
            "/v1/files",
            headers={**ALICE, "Idempotency-Key": "burst-1"},
            files={"file": ("same.bin", io.BytesIO(b"identical payload"), "application/octet-stream")},
        )
        return r.status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(attempt, range(16)))

    assert statuses.count(201) == 1, statuses
    assert set(statuses) <= {200, 201}
    with pg_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM files")).scalar() == 1
        assert conn.execute(text("SELECT count(*) FROM audit_events WHERE event_type='file.uploaded'")).scalar() == 1


def test_statement_timeout_is_applied(pg_app, pg_client: TestClient) -> None:  # type: ignore[no-untyped-def]
    with pg_app.state.engine.connect() as conn:
        assert conn.execute(text("SHOW statement_timeout")).scalar() == "5s"
