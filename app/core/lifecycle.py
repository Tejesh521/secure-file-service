"""Application lifecycle: startup checks, readiness state, graceful shutdown.

Startup order matters. We validate configuration, connect the database engine,
verify that the private storage directory is writable and only then mark the
process ready. On shutdown we flip readiness off first so a platform health check
stops routing traffic, then let in-flight requests finish and release resources.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from app.core.logging import configure_logging
from app.core.security import ApiKeyAuthenticator, UrlSigner
from app.core.telemetry import Metrics, configure_tracing
from app.infrastructure.database.session import create_engine_from_settings, create_session_factory
from app.infrastructure.storage.local import LocalFileStorage

if TYPE_CHECKING:
    from fastapi import FastAPI

    from app.core.config import Settings

logger = logging.getLogger(__name__)


class ReadinessState:
    """Mutable readiness flag shared between lifecycle and the readiness probe."""

    def __init__(self) -> None:
        self.ready = False
        self.started_at = time.time()
        self.shutting_down = False


def build_lifespan(settings: Settings):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level, settings.log_format, settings.app_name, settings.environment)
        state = ReadinessState()
        app.state.settings = settings
        app.state.readiness = state
        # Reuse the registry created by the app factory so middleware and handlers share it.
        app.state.metrics = getattr(app.state, "metrics", None) or Metrics()
        app.state.signer = UrlSigner(settings.signing_key_ring, settings.active_signing_key_id)
        app.state.authenticator = ApiKeyAuthenticator(settings.api_key_map)

        engine = create_engine_from_settings(settings, app.state.metrics)
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)

        storage = LocalFileStorage(settings.storage_root, chunk_size=settings.upload_chunk_bytes)
        storage.ensure_ready()
        app.state.storage = storage

        configure_tracing(app, settings, engine)

        state.ready = True
        logger.info(
            "service started",
            extra={
                "environment": settings.environment,
                "storage_root": str(settings.storage_root),
                "pid": os.getpid(),
                "active_signing_key": settings.active_signing_key_id,
            },
        )
        try:
            yield
        finally:
            state.ready = False
            state.shutting_down = True
            logger.info("shutdown started: readiness disabled, draining in-flight requests")
            engine.dispose()
            logging.shutdown()

    return lifespan
