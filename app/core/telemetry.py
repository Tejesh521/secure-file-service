"""Metrics and (optional) tracing.

Prometheus metrics are always available at ``/metrics``. OpenTelemetry tracing is
opt-in via ``OTEL_ENABLED`` and requires the ``otel`` extra; the import is lazy so
the base image stays small and the service never fails to start because a tracing
backend is unreachable.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

    from app.core.config import Settings

logger = logging.getLogger(__name__)

_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


class Metrics:
    """All service metrics in one place so names stay consistent and documented."""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        self.http_requests_total = Counter(
            "http_requests_total", "HTTP requests", ["method", "route", "status_code"], registry=self.registry
        )
        self.http_request_duration_seconds = Histogram(
            "http_request_duration_seconds",
            "HTTP request latency",
            ["method", "route"],
            buckets=_LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.http_requests_in_progress = Gauge(
            "http_requests_in_progress", "In-flight HTTP requests", ["method"], registry=self.registry
        )
        self.files_uploaded_total = Counter("files_uploaded_total", "Files stored", registry=self.registry)
        self.upload_bytes_total = Counter("upload_bytes_total", "Bytes accepted", registry=self.registry)
        self.upload_failures_total = Counter(
            "upload_failures_total", "Rejected or failed uploads", ["reason"], registry=self.registry
        )
        self.links_generated_total = Counter("links_generated_total", "Signed links issued", registry=self.registry)
        self.downloads_total = Counter("downloads_total", "Downloads served", registry=self.registry)
        self.download_failures_total = Counter(
            "download_failures_total", "Rejected downloads", ["reason"], registry=self.registry
        )
        self.files_deleted_total = Counter("files_deleted_total", "Files deleted", registry=self.registry)
        self.db_query_duration_seconds = Histogram(
            "db_query_duration_seconds",
            "Database statement latency",
            buckets=_LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.db_pool_connections = Gauge(
            "db_pool_connections", "Connections currently checked out", registry=self.registry
        )

    def render(self) -> tuple[bytes, str]:
        return generate_latest(self.registry), CONTENT_TYPE_LATEST


def configure_tracing(app: FastAPI, settings: Settings, engine: Any) -> None:
    """Enable OpenTelemetry if requested and available. Never fatal."""
    if not settings.otel_enabled:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:  # pragma: no cover - depends on optional extra
        logger.warning("OTEL_ENABLED is set but the 'otel' extra is not installed; tracing disabled")
        return

    provider = TracerProvider(resource=Resource.create({"service.name": settings.otel_service_name}))
    exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint or None)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, excluded_urls="health/live,health/ready,metrics")
    SQLAlchemyInstrumentor().instrument(engine=engine)
    logger.info("tracing enabled", extra={"otel_endpoint": settings.otel_exporter_otlp_endpoint})
