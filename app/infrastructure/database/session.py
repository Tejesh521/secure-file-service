"""Engine and session construction.

Timeouts are configured here so that no database call can wait forever: connect
timeout for establishing a connection, statement timeout (PostgreSQL) for queries.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.core.telemetry import Metrics

logger = logging.getLogger(__name__)


def create_engine_from_settings(settings: Settings, metrics: Metrics | None = None) -> Engine:
    url = settings.database_url
    kwargs: dict[str, Any] = {"pool_pre_ping": True, "future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url or url.endswith("sqlite://"):
            kwargs["poolclass"] = StaticPool
    else:
        kwargs.update(
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_connect_timeout_seconds,
            connect_args={
                "connect_timeout": settings.database_connect_timeout_seconds,
                "options": f"-c statement_timeout={settings.database_statement_timeout_ms}",
            },
        )
    engine = create_engine(url, **kwargs)
    if metrics is not None:
        _instrument(engine, metrics)
    return engine


def _instrument(engine: Engine, metrics: Metrics) -> None:
    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn: Any, cursor: Any, statement: Any, parameters: Any, context: Any, executemany: Any) -> None:
        conn.info.setdefault("query_start", []).append(time.perf_counter())

    @event.listens_for(engine, "after_cursor_execute")
    def _after(conn: Any, cursor: Any, statement: Any, parameters: Any, context: Any, executemany: Any) -> None:
        starts = conn.info.get("query_start")
        if starts:
            metrics.db_query_duration_seconds.observe(time.perf_counter() - starts.pop())

    @event.listens_for(engine, "checkout")
    def _checkout(dbapi_conn: Any, record: Any, proxy: Any) -> None:
        metrics.db_pool_connections.inc()

    @event.listens_for(engine, "checkin")
    def _checkin(dbapi_conn: Any, record: Any) -> None:
        metrics.db_pool_connections.dec()


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_database(engine: Engine) -> None:
    """Raise if the database cannot answer a trivial query."""
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
