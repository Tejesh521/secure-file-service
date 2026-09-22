"""PostgreSQL-backed fixtures.

Set ``TEST_DATABASE_URL`` (e.g. ``postgresql+psycopg://postgres:postgres@localhost:5432/files_test``)
to run these tests; otherwise they are skipped. The schema is created with the real
Alembic migrations so the tests also prove the migrations work.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.main import create_app
from tests.conftest import PUBLIC_BASE_URL, make_settings

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.integration


def alembic_config(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    # env.py reads this attribute first, so an exported DATABASE_URL (e.g. from the
    # Makefile) can never redirect the test migrations at another database.
    cfg.attributes["sqlalchemy.url"] = url
    return cfg


@pytest.fixture(scope="session")
def database_url() -> str:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set; skipping PostgreSQL integration tests")
    if not TEST_DATABASE_URL.startswith("postgresql"):
        pytest.skip("integration tests require a PostgreSQL TEST_DATABASE_URL")
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
def migrated_engine(database_url: str) -> Iterator[Engine]:
    """Start from an empty schema, migrate to head, tear everything down afterwards."""
    cfg = alembic_config(database_url)
    engine = create_engine(database_url, future=True)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    command.upgrade(cfg, "head")
    try:
        yield engine
    finally:
        command.downgrade(cfg, "base")
        engine.dispose()


@pytest.fixture
def pg_engine(migrated_engine: Engine) -> Iterator[Engine]:
    yield migrated_engine
    with migrated_engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE audit_events, files"))


@pytest.fixture
def pg_session(pg_engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(bind=pg_engine, expire_on_commit=False, autoflush=False)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def pg_app(tmp_path: Path, database_url: str, pg_engine: Engine) -> FastAPI:
    return create_app(make_settings(tmp_path, database_url=database_url))


@pytest.fixture
def pg_client(pg_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(pg_app, base_url=PUBLIC_BASE_URL) as client:
        yield client
