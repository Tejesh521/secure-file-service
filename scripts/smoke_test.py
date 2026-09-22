"""Post-deployment smoke test.

Exercises the whole request lifecycle against a running instance:

    python scripts/smoke_test.py https://your-app.ondigitalocean.app

The API key is read from ``SMOKE_API_KEY`` (defaults to the development key).
Exit code 0 means every check passed; any failure prints the offending step and
exits 1 so CI/CD can gate on it.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
import uuid
from urllib.parse import urlparse, urlunparse

import httpx


class SmokeFailure(Exception):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)
    print(f"  ok  {message}")


def rebase(url: str, base: str) -> str:
    """Point a signed URL at ``base`` when PUBLIC_BASE_URL differs from the address under test."""
    signed, target = urlparse(url), urlparse(base)
    return urlunparse((target.scheme, target.netloc, signed.path, signed.params, signed.query, signed.fragment))


def run(base_url: str, api_key: str, timeout: float) -> None:
    headers = {"X-API-Key": api_key}
    content = f"smoke {uuid.uuid4()}".encode()
    with httpx.Client(base_url=base_url, timeout=timeout, follow_redirects=False) as client:
        print("health")
        live = client.get("/health/live")
        check(live.status_code == 200, "/health/live returns 200")
        ready = client.get("/health/ready")
        check(ready.status_code == 200, f"/health/ready returns 200 {ready.json().get('checks')}")
        check("x-request-id" in ready.headers, "responses carry X-Request-ID")

        print("authentication")
        anon = client.get("/v1/files")
        check(anon.status_code == 401, "owner endpoints reject missing API key")
        check(anon.json()["error"]["code"] == "UNAUTHORIZED", "error envelope has code UNAUTHORIZED")

        print("upload")
        up = client.post("/v1/files", headers=headers, files={"file": ("smoke.txt", content, "text/plain")})
        check(up.status_code == 201, f"upload returns 201 (got {up.status_code}: {up.text[:200]})")
        file_id = up.json()["id"]
        check(up.json()["sha256"] == hashlib.sha256(content).hexdigest(), "server-side sha256 matches")

        print("metadata")
        meta = client.get(f"/v1/files/{file_id}", headers=headers)
        check(meta.status_code == 200 and meta.json()["size_bytes"] == len(content), "metadata readable by owner")

        print("signed link")
        link = client.post(f"/v1/files/{file_id}/links", headers=headers, json={"ttl_seconds": 120})
        check(link.status_code == 201, "link generation returns 201")
        url = rebase(link.json()["url"], base_url)

        print("download")
        down = client.get(url)
        check(down.status_code == 200 and down.content == content, "download via signed link returns the bytes")
        check(down.headers.get("content-disposition", "").startswith("attachment"), "download is served as attachment")
        tampered = url[:-1] + ("A" if url[-1] != "A" else "B")
        check(client.get(tampered).status_code == 403, "tampered signature rejected with 403")

        print("validation")
        bad_ttl = client.post(f"/v1/files/{file_id}/links", headers=headers, json={"ttl_seconds": 0})
        check(bad_ttl.status_code == 422, "ttl_seconds=0 rejected with 422")
        check(bad_ttl.json()["error"]["code"] == "VALIDATION_ERROR", "validation error uses the envelope")
        empty = client.post("/v1/files", headers=headers, files={"file": ("empty.txt", b"", "text/plain")})
        check(empty.status_code == 422 and empty.json()["error"]["code"] == "EMPTY_UPLOAD", "empty upload rejected")

        print("audit")
        audit = client.get(f"/v1/files/{file_id}/audit", headers=headers)
        types = [e["event_type"] for e in audit.json()["items"]]
        check("link.generated" in types and "file.downloaded" in types, f"audit trail recorded {types}")

        print("cleanup")
        check(client.delete(f"/v1/files/{file_id}", headers=headers).status_code == 204, "delete returns 204")
        check(client.get(url).status_code == 410, "link is dead after delete (410)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url", nargs="?", default="http://localhost:8080")
    parser.add_argument("--api-key", default=os.environ.get("SMOKE_API_KEY", "dev-key-alice"))
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--wait", type=float, default=0.0, help="seconds to wait for /health/ready before starting")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    if args.wait:
        deadline = time.monotonic() + args.wait
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"{base}/health/ready", timeout=3).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)

    started = time.perf_counter()
    try:
        run(base, args.api_key, args.timeout)
    except SmokeFailure as exc:
        print(f"\nFAIL: {exc}", file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(f"\nFAIL: transport error {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"\nPASS: smoke test completed in {time.perf_counter() - started:.2f}s against {base}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
