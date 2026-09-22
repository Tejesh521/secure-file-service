# syntax=docker/dockerfile:1.7
# Production image based on Ubuntu 24.04 LTS (Noble), using the distribution's Python 3.12.
# Two stages: the builder has venv/pip tooling; the runtime has only the interpreter, curl for
# the HEALTHCHECK, the virtualenv and the application, and runs as a dedicated non-root user.
#
# NOTE: image builds run in CI (GitHub-hosted runners). The local development environment has
# no Docker daemon; use `make local` (virtualenv + local PostgreSQL) there.

# ---------------------------------------------------------------------------
# Build stage
# ---------------------------------------------------------------------------
FROM ubuntu:24.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install dependencies first so this layer is cached while application code changes.
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --upgrade pip && pip install .

# ---------------------------------------------------------------------------
# Runtime stage
# ---------------------------------------------------------------------------
FROM ubuntu:24.04 AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8080 \
    STORAGE_ROOT=/var/lib/secure-file-service/storage

# python3: interpreter the virtualenv links against. curl: HEALTHCHECK only.
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 ca-certificates curl \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /app /var/lib/secure-file-service/storage \
    && chown -R app:app /app /var/lib/secure-file-service

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app app ./app
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app alembic.ini pyproject.toml ./
COPY --chown=app:app scripts/wait_for_db.py scripts/smoke_test.py ./scripts/

USER app
EXPOSE 8080
VOLUME ["/var/lib/secure-file-service/storage"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/health/live" || exit 1

# Production server: no --reload, bounded graceful shutdown, proxy headers from the platform edge.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*' --timeout-graceful-shutdown ${SHUTDOWN_TIMEOUT_SECONDS:-20} --no-access-log"]
