from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import upload


def test_get_metadata(client: TestClient, alice: dict[str, str]) -> None:
    created = upload(client, alice, b"abc", "a.txt").json()
    r = client.get(f"/v1/files/{created['id']}", headers=alice)
    assert r.status_code == 200
    assert r.json() == created


def test_other_owner_cannot_see_or_probe(client: TestClient, alice: dict[str, str], bob: dict[str, str]) -> None:
    file_id = upload(client, alice).json()["id"]
    assert client.get(f"/v1/files/{file_id}", headers=bob).status_code == 404
    assert client.get(f"/v1/files/{file_id}/audit", headers=bob).status_code == 404
    assert client.post(f"/v1/files/{file_id}/links", headers=bob).status_code == 404
    assert client.delete(f"/v1/files/{file_id}", headers=bob).status_code == 404
    # Identical body for "missing" and "not yours".
    missing = client.get("/v1/files/00000000-0000-0000-0000-000000000000", headers=bob).json()["error"]
    foreign = client.get(f"/v1/files/{file_id}", headers=bob).json()["error"]
    assert missing["code"] == foreign["code"] == "FILE_NOT_FOUND"
    assert missing["message"] == foreign["message"]


def test_list_is_scoped_paginated_and_newest_first(
    client: TestClient, alice: dict[str, str], bob: dict[str, str]
) -> None:
    ids = [upload(client, alice, f"f{i}".encode(), f"f{i}.txt").json()["id"] for i in range(5)]
    upload(client, bob, b"bob", "bob.txt")

    page = client.get("/v1/files?limit=2&offset=0", headers=alice).json()
    assert page["page"] == {"limit": 2, "offset": 0, "total": 5}
    assert [f["id"] for f in page["items"]] == ids[::-1][:2]
    assert all(f["owner_id"] == "alice" for f in page["items"])

    last = client.get("/v1/files?limit=2&offset=4", headers=alice).json()
    assert len(last["items"]) == 1 and last["items"][0]["id"] == ids[0]

    assert client.get("/v1/files?limit=101", headers=alice).status_code == 422


def test_delete_soft_deletes_and_blocks_new_links(client: TestClient, alice: dict[str, str]) -> None:
    file_id = upload(client, alice).json()["id"]
    assert client.delete(f"/v1/files/{file_id}", headers=alice).status_code == 204
    meta = client.get(f"/v1/files/{file_id}", headers=alice).json()
    assert meta["status"] == "deleted" and meta["deleted_at"] is not None
    r = client.post(f"/v1/files/{file_id}/links", headers=alice)
    assert r.status_code == 410 and r.json()["error"]["code"] == "FILE_UNAVAILABLE"
    # Idempotent: a second delete is still a 204.
    assert client.delete(f"/v1/files/{file_id}", headers=alice).status_code == 204


def test_audit_trail_records_every_event(client: TestClient, alice: dict[str, str]) -> None:
    file_id = upload(client, alice).json()["id"]
    link = client.post(f"/v1/files/{file_id}/links", headers=alice, json={"ttl_seconds": 30}).json()
    client.post(f"/v1/files/{file_id}/links", headers=alice)
    client.delete(f"/v1/files/{file_id}", headers=alice)

    r = client.get(f"/v1/files/{file_id}/audit", headers=alice)
    assert r.status_code == 200
    body = r.json()
    assert body["page"]["total"] == 4
    types = [e["event_type"] for e in body["items"]]
    assert types == ["file.deleted", "link.generated", "link.generated", "file.uploaded"]
    generated = [e for e in body["items"] if e["event_type"] == "link.generated"]
    matching = [e for e in generated if e["link_id"] == link["link_id"]]
    assert len(matching) == 1
    event = matching[0]
    assert event["actor_id"] == "alice"
    assert event["expires_at"] == link["expires_at"]
    assert event["metadata"] == {"ttl_seconds": 30, "key_id": "k1"}
    assert event["request_id"] and event["client_ip"]

    page = client.get(f"/v1/files/{file_id}/audit?limit=1&offset=3", headers=alice).json()
    assert [e["event_type"] for e in page["items"]] == ["file.uploaded"]
