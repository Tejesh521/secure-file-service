"""Migrations are reversible and the models never drift from the schema."""

from __future__ import annotations

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect

from app.infrastructure.database.base import Base
from tests.integration.conftest import alembic_config

pytestmark = pytest.mark.integration


def test_models_match_migrated_schema(migrated_engine) -> None:  # type: ignore[no-untyped-def]
    with migrated_engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        diff = compare_metadata(ctx, Base.metadata)
    assert diff == [], f"models and migrations have drifted: {diff}"


def test_downgrade_and_upgrade_roundtrip(database_url: str, migrated_engine) -> None:  # type: ignore[no-untyped-def]
    cfg = alembic_config(database_url)
    command.downgrade(cfg, "base")
    assert set(inspect(migrated_engine).get_table_names()) == {"alembic_version"}
    command.upgrade(cfg, "head")
    assert {"files", "audit_events"} <= set(inspect(migrated_engine).get_table_names())
