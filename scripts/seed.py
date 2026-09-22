"""Seed a running instance with sample files for demos.

    python scripts/seed.py http://localhost:8080 --api-key dev-key-alice --count 3

Uses the public API only, so it works against any environment you hold a key for.
"""

from __future__ import annotations

import argparse
import os

import httpx

SAMPLES = [
    ("quarterly-report.pdf", b"%PDF-1.7\n% sample report\n", "application/pdf"),
    ("team-photo.png", b"\x89PNG\r\n\x1a\n sample image bytes", "image/png"),
    ("notes.md", b"# Notes\n\nSeeded by scripts/seed.py\n", "text/markdown"),
    ("data.csv", b"id,value\n1,alpha\n2,beta\n", "text/csv"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url", nargs="?", default="http://localhost:8080")
    parser.add_argument("--api-key", default=os.environ.get("SEED_API_KEY", "dev-key-alice"))
    parser.add_argument("--count", type=int, default=len(SAMPLES))
    args = parser.parse_args()

    headers = {"X-API-Key": args.api_key}
    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=15) as client:
        for name, content, ctype in SAMPLES[: args.count]:
            r = client.post(
                "/v1/files",
                headers={**headers, "Idempotency-Key": f"seed-{name}"},
                files={"file": (name, content, ctype)},
            )
            r.raise_for_status()
            body = r.json()
            link = client.post(f"/v1/files/{body['id']}/links", headers=headers, json={"ttl_seconds": 3600})
            link.raise_for_status()
            status = "created" if r.status_code == 201 else "existing"
            print(f"{status:8} {body['id']}  {name:22} {body['size_bytes']:>6} B")
            print(f"         {link.json()['url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
