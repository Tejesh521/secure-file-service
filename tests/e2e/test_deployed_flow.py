"""End-to-end test against a deployed (or compose-started) instance.

Skipped unless ``E2E_BASE_URL`` is set. ``E2E_API_KEY`` defaults to the dev key.
Runs the same lifecycle the smoke test exercises, but as pytest assertions.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import Iterator
from urllib.parse import urlparse, urlunparse

import httpx
import pytest

BASE_URL = os.environ.get("E2E_BASE_URL", "").rstrip("/")
API_KEY = os.environ.get("E2E_API_KEY", "dev-key-alice")

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def client() -> Iterator[httpx.Client]:
    if not BASE_URL:
        pytest.skip("E2E_BASE_URL not set")
    with httpx.Client(base_url=BASE_URL, timeout=20, headers={"X-API-Key": API_KEY}) as c:
        yield c


def rebase(url: str) -> str:
    signed, target = urlparse(url), urlparse(BASE_URL)
    return urlunparse((target.scheme, target.netloc, signed.path, "", signed.query, ""))


def test_health(client: httpx.Client) -> None:
    assert client.get("/health/live").status_code == 200
    ready = client.get("/health/ready")
    assert ready.status_code == 200, ready.text
    assert ready.json()["checks"] == {"startup": "ok", "database": "ok", "storage": "ok"}


def test_full_lifecycle(client: httpx.Client) -> None:
    content = f"e2e {uuid.uuid4()}".encode()
    up = client.post("/v1/files", files={"file": ("e2e.txt", content, "text/plain")})
    assert up.status_code == 201, up.text
    file_id = up.json()["id"]
    assert up.json()["sha256"] == hashlib.sha256(content).hexdigest()

    link = client.post(f"/v1/files/{file_id}/links", json={"ttl_seconds": 90})
    assert link.status_code == 201, link.text
    url = rebase(link.json()["url"])

    anonymous = httpx.Client(timeout=20)
    down = anonymous.get(url)
    assert down.status_code == 200 and down.content == content
    assert anonymous.get(url[:-1] + ("A" if url[-1] != "A" else "B")).status_code == 403

    audit = client.get(f"/v1/files/{file_id}/audit").json()["items"]
    assert {"file.uploaded", "link.generated", "file.downloaded"} <= {e["event_type"] for e in audit}

    assert client.delete(f"/v1/files/{file_id}").status_code == 204
    assert anonymous.get(url).status_code == 410


def test_validation_and_auth(client: httpx.Client) -> None:
    assert httpx.get(f"{BASE_URL}/v1/files", timeout=20).status_code == 401
    bad = client.post("/v1/files", files={"file": ("empty.txt", b"", "text/plain")})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "EMPTY_UPLOAD"
