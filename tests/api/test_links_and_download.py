"""Signed link generation and the public download endpoint."""

from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.infrastructure.database.base import Base
from app.main import create_app
from tests.conftest import PUBLIC_BASE_URL, make_settings, signed_path, upload


def make_link(client: TestClient, headers: dict[str, str], file_id: str, ttl: int | None = None) -> dict:  # type: ignore[type-arg]
    body = {"ttl_seconds": ttl} if ttl is not None else None
    r = client.post(f"/v1/files/{file_id}/links", headers=headers, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_create_link_shape(client: TestClient, alice: dict[str, str]) -> None:
    file_id = upload(client, alice).json()["id"]
    link = make_link(client, alice, file_id, ttl=120)
    assert link["file_id"] == file_id and link["ttl_seconds"] == 120 and link["key_id"] == "k1"
    parsed = urlparse(link["url"])
    assert link["url"].startswith(PUBLIC_BASE_URL)
    assert parsed.path == f"/v1/download/{file_id}"
    q = parse_qs(parsed.query)
    assert set(q) == {"exp", "lid", "kid", "sig"}
    assert q["lid"] == [link["link_id"]]
    assert abs(int(q["exp"][0]) - (time.time() + 120)) < 5


def test_link_ttl_defaults_and_bounds(client: TestClient, alice: dict[str, str]) -> None:
    file_id = upload(client, alice).json()["id"]
    assert make_link(client, alice, file_id)["ttl_seconds"] == 600
    for ttl in (0, -1):
        r = client.post(f"/v1/files/{file_id}/links", headers=alice, json={"ttl_seconds": ttl})
        assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    r = client.post(f"/v1/files/{file_id}/links", headers=alice, json={"ttl_seconds": 3601})
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_TTL"
    r = client.post(f"/v1/files/{file_id}/links", headers=alice, json={"ttl_seconds": 10, "extra": 1})
    assert r.status_code == 422


def test_link_for_unknown_file(client: TestClient, alice: dict[str, str]) -> None:
    assert client.post("/v1/files/nope/links", headers=alice).status_code == 404


def test_download_with_valid_link(client: TestClient, alice: dict[str, str]) -> None:
    content = b"%PDF-1.4 fake"
    created = upload(client, alice, content, "réport.pdf", "application/pdf").json()
    link = make_link(client, alice, created["id"])

    r = client.get(signed_path(link["url"]))  # no API key: public endpoint
    assert r.status_code == 200
    assert r.content == content
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-length"] == str(len(content))
    assert r.headers["content-disposition"].startswith("attachment;")
    assert "filename*=UTF-8''r%C3%A9port.pdf" in r.headers["content-disposition"]
    assert r.headers["cache-control"] == "private, no-store"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["etag"] == f'"{created["sha256"]}"'

    audit = client.get(f"/v1/files/{created['id']}/audit", headers=alice).json()["items"]
    downloaded = [e for e in audit if e["event_type"] == "file.downloaded"]
    assert len(downloaded) == 1 and downloaded[0]["link_id"] == link["link_id"]


def test_link_can_be_used_repeatedly_until_expiry(client: TestClient, alice: dict[str, str]) -> None:
    file_id = upload(client, alice).json()["id"]
    path = signed_path(make_link(client, alice, file_id)["url"])
    assert client.get(path).status_code == 200
    assert client.get(path).status_code == 200


def test_tampered_signature_rejected(client: TestClient, alice: dict[str, str]) -> None:
    file_id = upload(client, alice).json()["id"]
    path = signed_path(make_link(client, alice, file_id)["url"])
    tampered = path[:-1] + ("A" if path[-1] != "A" else "B")
    r = client.get(tampered)
    assert r.status_code == 403 and r.json()["error"]["code"] == "LINK_SIGNATURE_INVALID"


def test_extending_expiry_in_url_rejected(client: TestClient, alice: dict[str, str]) -> None:
    file_id = upload(client, alice).json()["id"]
    url = make_link(client, alice, file_id)["url"]
    q = parse_qs(urlparse(url).query)
    path = f"/v1/download/{file_id}?exp={int(q['exp'][0]) + 999999}&lid={q['lid'][0]}&kid=k1&sig={q['sig'][0]}"
    assert client.get(path).status_code == 403


def test_link_for_one_file_cannot_fetch_another(client: TestClient, alice: dict[str, str]) -> None:
    a = upload(client, alice, b"A", "a.txt").json()["id"]
    b = upload(client, alice, b"B", "b.txt").json()["id"]
    path = signed_path(make_link(client, alice, a)["url"]).replace(a, b)
    assert client.get(path).status_code == 403


def test_expired_link(app: FastAPI, client: TestClient, alice: dict[str, str]) -> None:
    file_id = upload(client, alice).json()["id"]
    exp = int(time.time()) - 10
    sig = app.state.signer.sign(file_id, "lid-1", exp)
    r = client.get(f"/v1/download/{file_id}?exp={exp}&lid=lid-1&kid={sig.key_id}&sig={sig.value}")
    assert r.status_code == 410 and r.json()["error"]["code"] == "LINK_EXPIRED"


def test_unknown_key_id(client: TestClient, alice: dict[str, str]) -> None:
    file_id = upload(client, alice).json()["id"]
    path = signed_path(make_link(client, alice, file_id)["url"]).replace("kid=k1", "kid=k9")
    assert client.get(path).status_code == 403


def test_link_signed_with_rotated_key_still_valid(app: FastAPI, client: TestClient, alice: dict[str, str]) -> None:
    from app.core.security import UrlSigner

    file_id = upload(client, alice).json()["id"]
    exp = int(time.time()) + 60
    old_signer = UrlSigner({"k0": app.state.signer._keys["k0"]}, "k0")
    sig = old_signer.sign(file_id, "lid-old", exp)
    r = client.get(f"/v1/download/{file_id}?exp={exp}&lid=lid-old&kid=k0&sig={sig.value}")
    assert r.status_code == 200


def test_deleted_file_link_is_gone(client: TestClient, alice: dict[str, str]) -> None:
    file_id = upload(client, alice).json()["id"]
    path = signed_path(make_link(client, alice, file_id)["url"])
    client.delete(f"/v1/files/{file_id}", headers=alice)
    r = client.get(path)
    assert r.status_code == 410 and r.json()["error"]["code"] == "FILE_UNAVAILABLE"


def test_properly_signed_link_for_missing_file(app: FastAPI, client: TestClient) -> None:
    exp = int(time.time()) + 60
    sig = app.state.signer.sign("ghost", "lid", exp)
    r = client.get(f"/v1/download/ghost?exp={exp}&lid=lid&kid=k1&sig={sig.value}")
    assert r.status_code == 404


def test_malformed_query_parameters(client: TestClient, alice: dict[str, str]) -> None:
    file_id = upload(client, alice).json()["id"]
    assert client.get(f"/v1/download/{file_id}").status_code == 422
    assert client.get(f"/v1/download/{file_id}?exp=abc&lid=x&kid=k1&sig={'A' * 43}").status_code == 422
    assert client.get(f"/v1/download/{file_id}?exp=1&lid=x&kid=k1&sig=short").status_code == 422
    assert client.get(f"/v1/download/{file_id}?exp=1&lid=x&kid=k1&sig={'A' * 42}%00").status_code == 422


def test_missing_bytes_is_internal_error_without_leak(app: FastAPI, client: TestClient, alice: dict[str, str]) -> None:
    file_id = upload(client, alice).json()["id"]
    path = signed_path(make_link(client, alice, file_id)["url"])
    for p in Path(app.state.storage.root).rglob("*"):
        if p.is_file():
            p.unlink()
    r = client.get(path)
    assert r.status_code == 500
    body = r.json()["error"]
    assert body["code"] == "STORAGE_INCONSISTENT" and body["message"] == "Internal server error."


def test_link_survives_service_restart(tmp_path: Path, alice: dict[str, str]) -> None:
    """A link minted by one process is valid in a brand-new process with the same config."""
    db = f"sqlite:///{tmp_path / 'restart.db'}"
    settings = make_settings(tmp_path, database_url=db)

    first = create_app(settings)
    with TestClient(first, base_url=PUBLIC_BASE_URL) as c1:
        Base.metadata.create_all(first.state.engine)
        file_id = upload(c1, alice, b"persist me").json()["id"]
        url = make_link(c1, alice, file_id)["url"]
    # ``first`` is now shut down; nothing in memory survives.

    second = create_app(make_settings(tmp_path, database_url=db))
    with TestClient(second, base_url=PUBLIC_BASE_URL) as c2:
        r = c2.get(signed_path(url))
        assert r.status_code == 200 and r.content == b"persist me"
