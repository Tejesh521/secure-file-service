from __future__ import annotations

import hashlib

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import upload


def test_upload_returns_metadata_and_location(client: TestClient, alice: dict[str, str], app: FastAPI) -> None:
    content = b"hello world"
    r = upload(client, alice, content, "notes.txt", "text/plain")
    assert r.status_code == 201
    body = r.json()
    assert r.headers["location"] == f"/v1/files/{body['id']}"
    assert body["owner_id"] == "alice"
    assert body["filename"] == "notes.txt"
    assert body["content_type"] == "text/plain"
    assert body["size_bytes"] == len(content)
    assert body["sha256"] == hashlib.sha256(content).hexdigest()
    assert body["status"] == "available"
    assert body["deleted_at"] is None
    assert body["created_at"].endswith("Z")
    # Bytes live under the private storage root, never under a public path.
    stored = [p for p in app.state.storage.root.rglob("*") if p.is_file()]
    assert len(stored) == 1 and stored[0].read_bytes() == content


def test_upload_sanitises_filename_and_content_type(client: TestClient, alice: dict[str, str]) -> None:
    r = upload(client, alice, b"x", "../../etc/passwd", "garbage")
    assert r.status_code == 201
    assert r.json()["filename"] == "passwd"
    assert r.json()["content_type"] == "application/octet-stream"


def test_upload_requires_multipart_file_field(client: TestClient, alice: dict[str, str]) -> None:
    r = client.post("/v1/files", headers=alice, data={"file": "not a file"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_upload_without_body_is_validation_error(client: TestClient, alice: dict[str, str]) -> None:
    r = client.post("/v1/files", headers=alice)
    assert r.status_code == 422


def test_empty_file_rejected(client: TestClient, alice: dict[str, str]) -> None:
    r = upload(client, alice, b"", "empty.txt")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "EMPTY_UPLOAD"


def test_oversized_file_rejected_via_content_length(client: TestClient, alice: dict[str, str]) -> None:
    r = upload(client, alice, b"x" * (2 * 1024 * 1024), "big.bin")
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_oversized_file_rejected_when_streamed(client: TestClient, alice: dict[str, str]) -> None:
    """Content-Length is absent (chunked), so the limit must be enforced while reading."""
    boundary = "b0undary"
    head = f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="big.bin"\r\n\r\n'.encode()
    tail = f"\r\n--{boundary}--\r\n".encode()

    def body():  # type: ignore[no-untyped-def]
        yield head
        for _ in range(1200):
            yield b"x" * 1024
        yield tail

    r = client.post(
        "/v1/files",
        headers={**alice, "Content-Type": f"multipart/form-data; boundary={boundary}", "Transfer-Encoding": "chunked"},
        content=body(),
    )
    assert r.status_code == 413


def test_upload_requires_auth(client: TestClient) -> None:
    assert upload(client, {}).status_code == 401


def test_idempotent_replay(client: TestClient, alice: dict[str, str]) -> None:
    first = upload(client, alice, b"same", "a.txt", **{"Idempotency-Key": "order-42"})
    second = upload(client, alice, b"same", "a.txt", **{"Idempotency-Key": "order-42"})
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    listing = client.get("/v1/files", headers=alice).json()
    assert listing["page"]["total"] == 1


def test_idempotency_conflict(client: TestClient, alice: dict[str, str]) -> None:
    upload(client, alice, b"one", **{"Idempotency-Key": "order-43"})
    r = upload(client, alice, b"two", **{"Idempotency-Key": "order-43"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_idempotency_key_scoped_per_owner(client: TestClient, alice: dict[str, str], bob: dict[str, str]) -> None:
    a = upload(client, alice, b"one", **{"Idempotency-Key": "shared"})
    b = upload(client, bob, b"two", **{"Idempotency-Key": "shared"})
    assert a.status_code == 201 and b.status_code == 201


def test_idempotency_key_format_validated(client: TestClient, alice: dict[str, str]) -> None:
    r = upload(client, alice, b"x", **{"Idempotency-Key": "has spaces!"})
    assert r.status_code == 422
    assert r.json()["error"]["details"][0]["field"] == "header.Idempotency-Key"
