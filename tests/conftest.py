"""Shared fixtures.

The API and unit suites run against an in-memory SQLite database and a temporary
storage directory so they need no external services. The integration suite (see
``tests/integration/conftest.py``) uses real PostgreSQL when ``TEST_DATABASE_URL``
is set.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.infrastructure.database.base import Base
from app.main import create_app

ALICE_KEY = "test-key-alice"
BOB_KEY = "test-key-bob"
SIGNING_KEYS = "k1:unit-test-signing-secret-0123456789abcdef,k0:older-rotated-secret-0123456789abcdef"
PUBLIC_BASE_URL = "http://files.test"


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "test",
        "log_format": "console",
        "log_level": "WARNING",
        "database_url": "sqlite://",
        "storage_root": tmp_path / "storage",
        "public_base_url": PUBLIC_BASE_URL,
        "signing_keys": SIGNING_KEYS,
        "signing_active_key_id": "k1",
        "api_keys": f"{ALICE_KEY}:alice,{BOB_KEY}:bob",
        "max_upload_bytes": 1024 * 1024,
        "max_request_body_bytes": 16 * 1024,
        "default_link_ttl_seconds": 600,
        "max_link_ttl_seconds": 3600,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """Runs the lifespan exactly once; ``app.state`` is populated while the client is open."""
    with TestClient(app, base_url=PUBLIC_BASE_URL) as test_client:
        # The lifespan created the engine; create the schema for the in-memory DB.
        Base.metadata.create_all(app.state.engine)
        yield test_client


@pytest.fixture
def alice() -> dict[str, str]:
    return {"X-API-Key": ALICE_KEY}


@pytest.fixture
def bob() -> dict[str, str]:
    return {"X-API-Key": BOB_KEY}


def upload(
    client: TestClient,
    headers: dict[str, str],
    content: bytes = b"hello world",
    filename: str = "hello.txt",
    content_type: str = "text/plain",
    **extra_headers: str,
) -> Any:
    return client.post(
        "/v1/files",
        headers={**headers, **extra_headers},
        files={"file": (filename, io.BytesIO(content), content_type)},
    )


def signed_path(url: str) -> str:
    """Strip the public base URL so the TestClient can request the path."""
    assert url.startswith(PUBLIC_BASE_URL)
    return url[len(PUBLIC_BASE_URL) :]
