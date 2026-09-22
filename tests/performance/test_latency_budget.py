"""Opt-in load-shaped test. Run with ``RUN_PERFORMANCE=1 pytest -m performance``.

Not a benchmark: it guards against gross regressions (e.g. an accidental O(n)
scan per request) by asserting generous p95 budgets on the in-process stack.
"""

from __future__ import annotations

import os
import statistics
import time

import pytest
from fastapi.testclient import TestClient

from tests.conftest import signed_path, upload

pytestmark = pytest.mark.performance

if not os.environ.get("RUN_PERFORMANCE"):
    pytest.skip("set RUN_PERFORMANCE=1 to run performance tests", allow_module_level=True)


def p95(samples: list[float]) -> float:
    return statistics.quantiles(samples, n=20)[-1]


def timed(fn) -> float:  # type: ignore[no-untyped-def]
    start = time.perf_counter()
    fn()
    return (time.perf_counter() - start) * 1000


def test_upload_sign_download_p95(client: TestClient, alice: dict[str, str]) -> None:
    payload = b"x" * 64 * 1024
    uploads, signs, downloads = [], [], []
    for _ in range(100):
        uploads.append(timed(lambda: upload(client, alice, payload, "perf.bin")))
    file_id = upload(client, alice, payload, "perf.bin").json()["id"]
    for _ in range(200):
        signs.append(timed(lambda: client.post(f"/v1/files/{file_id}/links", headers=alice)))
    url = signed_path(client.post(f"/v1/files/{file_id}/links", headers=alice).json()["url"])
    for _ in range(200):
        downloads.append(timed(lambda: client.get(url)))

    print(f"\np95 ms  upload={p95(uploads):.1f} sign={p95(signs):.1f} download={p95(downloads):.1f}")
    assert p95(uploads) < 250
    assert p95(signs) < 100
    assert p95(downloads) < 100


def test_listing_stays_flat_as_files_grow(client: TestClient, alice: dict[str, str]) -> None:
    for _ in range(300):
        upload(client, alice, b"tiny", "t.txt")
    samples = [timed(lambda: client.get("/v1/files?limit=20", headers=alice)) for _ in range(50)]
    assert p95(samples) < 100
