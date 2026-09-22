# Secure File Service

Private file uploads with cryptographically signed, time-limited download links.

Owners upload files over an authenticated API. Files are written to a non-public directory
under server-generated names and their metadata is stored in PostgreSQL. An owner can mint a
signed URL with a time-to-live; anyone holding that URL can download the file until it expires,
without credentials. Every link generation, upload, download and delete is recorded in an audit
trail the owner can query.

[![CI](https://github.com/Tejesh521/secure-file-service/actions/workflows/ci.yml/badge.svg)](.github/workflows/ci.yml)

---

## Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Database](#database)
- [API](#api)
- [Testing](#testing)
- [Observability](#observability)
- [Security](#security)
- [Deployment](#deployment)
- [Design Decisions](#design-decisions)
- [Assumptions](#assumptions)
- [Known Limitations](#known-limitations)
- [Scaling Strategy](#scaling-strategy)
- [Improvements With More Time](#improvements-with-more-time)
- [Repository Layout](#repository-layout)

## Overview

| Requirement | Where it lives |
|---|---|
| Secure file ingestion into a non-public directory, tied to a user | `POST /v1/files` → `app/application/commands/upload_file.py`, `app/infrastructure/storage/local.py` |
| Signer endpoint taking a file id and TTL, valid across restarts | `POST /v1/files/{id}/links` → `app/application/commands/create_signed_link.py`, `app/core/security.py` |
| Public retrieval endpoint validating signature and expiry | `GET /v1/download/{id}` → `app/application/services/download_service.py` |
| Owner metadata queries and audit event on every link generation | `GET /v1/files`, `GET /v1/files/{id}`, `GET /v1/files/{id}/audit` |
| Architecture flow diagram | [below](#architecture) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Validation, tests, CI/CD, documentation | `app/schemas`, `tests/`, `.github/workflows/`, `docs/` |

## Features

- **Streamed uploads** with size enforcement while reading, SHA-256 hashing, atomic rename into
  place, and server-generated object keys (path traversal is impossible by construction).
- **Signed links**: HMAC-SHA256 over `file_id`, `link_id` and expiry, with a key ring so keys can
  rotate without invalidating outstanding links. Secrets live in configuration, so links survive
  restarts and verify on any replica.
- **Fail-closed download path**: signature → expiry → metadata → file state → storage presence.
  A bad signature never touches the database.
- **Audit trail** per file: `file.uploaded`, `link.generated`, `file.downloaded`, `file.deleted`,
  each with actor, request id, client IP and event metadata.
- **Idempotent uploads** via `Idempotency-Key`, enforced by a database unique constraint so
  concurrent retries produce exactly one file.
- **One error envelope** for every non-2xx response, always carrying `X-Request-ID`.
- **Operable**: liveness and readiness probes, graceful shutdown, structured JSON logs,
  Prometheus metrics, optional OpenTelemetry tracing, bounded DB timeouts.
- **Delivery**: Alembic migrations, non-root multi-stage Ubuntu 24.04 image, Compose stack,
  DigitalOcean App Platform spec, GitHub Actions CI and security scanning.

## Architecture

![Architecture diagram](docs/diagrams/architecture.svg)

A standalone copy lives at [docs/diagrams/architecture.svg](docs/diagrams/architecture.svg) (source: [architecture.mmd](docs/diagrams/architecture.mmd)). The same flow as Mermaid:

```mermaid
flowchart LR
    subgraph Clients
        Owner[Owner<br/>X-API-Key]
        Anyone[Link holder<br/>no credentials]
    end

    subgraph Edge["DigitalOcean App Platform"]
        TLS[Ingress / TLS]
    end

    subgraph API["FastAPI service (stateless)"]
        MW[Middleware<br/>request id · body limit · access log · metrics]
        AUTH[API key auth]
        UP[UploadFileHandler]
        SIGN[CreateSignedLinkHandler]
        DL[DownloadService]
        Q[Queries<br/>get · list · audit]
    end

    subgraph Data
        PG[(PostgreSQL<br/>files · audit_events)]
        FS[/Private storage root<br/>server-generated keys/]
    end

    Owner -->|POST /v1/files| TLS
    Owner -->|POST /v1/files/{id}/links| TLS
    Owner -->|GET /v1/files…| TLS
    Anyone -->|GET /v1/download/{id}?exp&lid&kid&sig| TLS
    TLS --> MW --> AUTH
    AUTH --> UP & SIGN & Q
    MW -->|public route| DL
    UP -->|1 stream + hash| FS
    UP -->|2 metadata + audit, one txn| PG
    SIGN -->|HMAC-SHA256| SIGN
    SIGN -->|audit link.generated| PG
    DL -->|verify sig · check exp| DL
    DL -->|lookup · audit download| PG
    DL -->|stream bytes| FS
```

Request lifecycle for the two critical paths:

```
UPLOAD                                    DOWNLOAD (signed URL)
──────                                    ─────────────────────
POST /v1/files (multipart)                GET /v1/download/{id}?exp&lid&kid&sig
  │                                         │
  ├─ body-size limit (Content-Length + streaming)
  ├─ API key → owner_id                     ├─ parse & shape-validate query params (422)
  ├─ stream to storage/.tmp, hash, enforce  ├─ HMAC verify with key ring      ──► 403
  │  MAX_UPLOAD_BYTES, fsync, atomic rename ├─ expiry check (server clock)    ──► 410
  ├─ Idempotency-Key lookup → replay/409    ├─ load metadata                  ──► 404
  ├─ INSERT files + audit(file.uploaded)    ├─ status must be available       ──► 410
  │  in one transaction (UNIQUE arbitrates  ├─ object must exist on disk      ──► 500
  │  concurrent retries)                    ├─ INSERT audit(file.downloaded), commit
  └─ 201 + Location + metadata JSON         └─ 200 stream bytes, attachment, no-store
```

Evolution path when the workload grows (details in [docs/SCALING.md](docs/SCALING.md)):

```
VERSION 1 (this repo)              HIGHER SCALE
API ── PostgreSQL                  N × API ── PostgreSQL (managed, replicas)
    └─ local private disk               └─ object storage (Spaces/S3) behind the same FileStorage port
                                        └─ durable queue → workers (virus scan, thumbnails, retention)
```

The full request/data-flow narrative, component responsibilities and domain model are in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Tech Stack

| Concern | Choice |
|---|---|
| API / validation | FastAPI, Pydantic v2 |
| Server | Uvicorn |
| Database / ORM / migrations | PostgreSQL 16, SQLAlchemy 2.x, Alembic |
| Signing | HMAC-SHA256 (`hmac`, `hashlib`, constant-time compare) |
| Tests | pytest, httpx TestClient, jsonschema, real PostgreSQL in CI |
| Lint / format / types | Ruff, Ruff, mypy `--strict` |
| Logs / metrics / traces | JSON logging, prometheus-client, optional OpenTelemetry |
| Packaging / container | `pyproject.toml`, multi-stage Docker image on Ubuntu 24.04 LTS (non-root) |
| CI / deployment | GitHub Actions, DigitalOcean App Platform (`.do/app.yaml`) |

Deliberately **not** in version 1: Redis, Celery, Kafka, Kubernetes. See
[docs/TRADEOFFS.md](docs/TRADEOFFS.md).

## Quick Start

### Option A: Local Python + local PostgreSQL (no Docker needed)

This is the path used in the development environment, which has no Docker daemon.

```bash
git clone https://github.com/Tejesh521/secure-file-service.git
cd secure-file-service
make install                            # python3 -m venv .venv && pip install -e ".[dev]"
cp .env.example .env
createdb files                          # or: psql -U postgres -c 'CREATE DATABASE files'
make local                              # waits for PostgreSQL, runs migrations, starts uvicorn on :8080
```

In another terminal:

```bash
make smoke          # full lifecycle check against http://localhost:8080
open http://localhost:8080/docs
```

### Option B: Docker Compose (where a Docker daemon exists)

```bash
make dev            # builds the Ubuntu 24.04 image, starts PostgreSQL, runs migrations, API on :8080
```

### Try it

```bash
# Upload (development API key maps to user "alice")
curl -s -X POST localhost:8080/v1/files \
  -H 'X-API-Key: dev-key-alice' -F 'file=@README.md' | tee /tmp/file.json

FILE_ID=$(python3 -c "import json;print(json.load(open('/tmp/file.json'))['id'])")

# Mint a 10-minute signed link (audit event recorded)
curl -s -X POST localhost:8080/v1/files/$FILE_ID/links \
  -H 'X-API-Key: dev-key-alice' -H 'Content-Type: application/json' \
  -d '{"ttl_seconds": 600}' | tee /tmp/link.json

# Download with no credentials
URL=$(python3 -c "import json;print(json.load(open('/tmp/link.json'))['url'])")
curl -sD - -o /tmp/downloaded "$URL" | head -12

# Owner views metadata and audit trail
curl -s localhost:8080/v1/files/$FILE_ID -H 'X-API-Key: dev-key-alice'
curl -s localhost:8080/v1/files/$FILE_ID/audit -H 'X-API-Key: dev-key-alice'
```

## Configuration

All settings come from environment variables (or a local `.env`). See
[.env.example](.env.example) for the complete list with defaults. The important ones:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | SQLAlchemy URL, e.g. `postgresql+psycopg://user:pass@host:5432/files` |
| `STORAGE_ROOT` | Private directory for file bytes. Created `0700`. Never served statically. |
| `SIGNING_KEYS` | Key ring `id:secret,id:secret`. First (or `SIGNING_ACTIVE_KEY_ID`) signs; all verify. |
| `API_KEYS` | Owner credentials `apikey:userid,...`. |
| `PUBLIC_BASE_URL` | Prefix for generated links. Must be `https://` in production. |
| `DEFAULT_LINK_TTL_SECONDS`, `MAX_LINK_TTL_SECONDS` | Link lifetime default (1 h) and cap (7 d). |
| `MAX_UPLOAD_BYTES`, `MAX_REQUEST_BODY_BYTES` | Upload cap (100 MiB) and cap for everything else (1 MiB). |
| `DATABASE_*_TIMEOUT*`, `SHUTDOWN_TIMEOUT_SECONDS` | No operation waits forever. |
| `ENVIRONMENT=production` | Enables safety rails: refuses development secrets, short keys and non-HTTPS base URL. |

Generate a signing secret with `make secret`.

## Database

Two tables, created by `migrations/versions/0001_initial.py`:

```
files                                     audit_events
─────                                     ────────────
id                 VARCHAR(36) PK         id            VARCHAR(36) PK
owner_id           VARCHAR(128) NOT NULL  file_id       VARCHAR(36) FK files(id) ON DELETE CASCADE
original_filename  VARCHAR(255) NOT NULL  event_type    VARCHAR(64) NOT NULL
content_type       VARCHAR(255) NOT NULL  actor_id      VARCHAR(128) NULL   (null for anonymous downloads)
size_bytes         BIGINT NOT NULL        link_id       VARCHAR(36) NULL
sha256             VARCHAR(64) NOT NULL   expires_at    TIMESTAMPTZ NULL
storage_key        VARCHAR(512) UNIQUE    request_id    VARCHAR(128) NULL
status             VARCHAR(32) NOT NULL   client_ip     VARCHAR(64) NULL
idempotency_key    VARCHAR(255) NULL      metadata      JSONB NOT NULL
created_at         TIMESTAMPTZ NOT NULL   created_at    TIMESTAMPTZ NOT NULL
updated_at         TIMESTAMPTZ NOT NULL
deleted_at         TIMESTAMPTZ NULL
UNIQUE (owner_id, idempotency_key)
```

Indexes and why they exist:

| Index | Serves |
|---|---|
| `ix_files_owner_created (owner_id, created_at)` | "List my files, newest first" |
| `uq_files_owner_idempotency (owner_id, idempotency_key)` | Idempotent retries; the constraint is the arbiter for concurrent duplicates |
| `uq_files_storage_key` | Two rows can never claim the same object |
| `ix_audit_events_file_created (file_id, created_at)` | "Show this file's audit trail, newest first" |

Commands: `make migrate`, `make migration MSG="..."`, `make migration-check` (fails if models and
migrations drift). Deploys run `alembic upgrade head` as a pre-deploy job before the new version
takes traffic ([docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)).

## API

Interactive docs at `/docs`, machine-readable spec at `/openapi.json` and committed as
[openapi/openapi.yaml](openapi/openapi.yaml). Full reference with examples: [docs/API.md](docs/API.md).

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/v1/files` | API key | Upload (multipart field `file`, optional `Idempotency-Key`) → 201 |
| `GET` | `/v1/files` | API key | List own files, `limit`/`offset` |
| `GET` | `/v1/files/{file_id}` | API key | Metadata: filename, size, hash, status, dates |
| `DELETE` | `/v1/files/{file_id}` | API key | Soft delete; invalidates all links → 204 |
| `POST` | `/v1/files/{file_id}/links` | API key | Mint signed URL `{"ttl_seconds": 600}` → 201, audit event |
| `GET` | `/v1/files/{file_id}/audit` | API key | Audit trail, paginated |
| `GET` | `/v1/download/{file_id}?exp&lid&kid&sig` | signature | Public download → bytes as attachment |
| `GET` | `/health/live`, `/health/ready`, `/metrics` | none | Probes and Prometheus metrics |

Every error has the same shape:

```json
{"error": {"code": "LINK_EXPIRED", "message": "Signed link has expired.", "details": [], "request_id": "req_…"}}
```

## Testing

```bash
make test               # unit + API + contract, with coverage (no external services, ~2 s)
make test-integration   # real PostgreSQL: constraints, migrations, concurrency  (TEST_DATABASE_URL)
make test-e2e E2E_BASE_URL=https://your-app   # against a deployed instance
make test-performance   # opt-in latency budgets
make verify             # format, lint, types, OpenAPI snapshot, unit/API/contract, integration
```

Current state: 166 tests pass locally with 98% line coverage (`fail_under = 85`). Layout and
philosophy, including what is deliberately not tested: [docs/TESTING.md](docs/TESTING.md).

## Observability

- **Logs**: one JSON object per line with `request_id`, `user_id`, `route`, `status_code`,
  `duration_ms`. Secrets, keys and URLs with signatures are never logged.
- **Metrics** (`/metrics`): `http_requests_total`, `http_request_duration_seconds`,
  `http_requests_in_progress`, `files_uploaded_total`, `upload_bytes_total`,
  `upload_failures_total{reason}`, `links_generated_total`, `downloads_total`,
  `download_failures_total{reason}`, `files_deleted_total`, `db_query_duration_seconds`,
  `db_pool_connections`. Route labels are templates (`/v1/files/{file_id}`), never raw paths.
- **Correlation**: a valid client `X-Request-ID` is preserved, otherwise one is minted; it is
  echoed on every response and stamped on every audit event.
- **Tracing**: `OTEL_ENABLED=true` with the `otel` extra instruments FastAPI and SQLAlchemy.
- **Health**: `/health/live` (process only) vs `/health/ready` (startup done, DB reachable,
  storage writable). Readiness flips to 503 the moment SIGTERM arrives.

Dashboards, alerts and SLOs: [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md).

## Security

- Files are never served by path. Object keys are random and server-generated; the storage root
  is `0700` and outside any static mount; `path_for` refuses keys that escape the root.
- HMAC-SHA256 signatures with constant-time comparison; expiry is inside the signed message so it
  cannot be extended; key ids allow rotation without downtime.
- Owner endpoints require `X-API-Key` (constant-time lookup). Owners cannot probe other users'
  file ids: missing and foreign files return identical 404s.
- Strict input bounds: filename sanitisation, content-type allow-shape, TTL range, pagination
  caps, `Idempotency-Key` pattern, body-size limits both by `Content-Length` and while streaming.
- Downloads are `Content-Disposition: attachment`, `X-Content-Type-Options: nosniff`,
  `Cache-Control: private, no-store`.
- Production refuses to start with development secrets, short keys or a non-HTTPS base URL.
- Non-root container on Ubuntu 24.04 LTS with only the interpreter and curl at runtime, `pip-audit`, Trivy and gitleaks in CI.

Threat model and residual risks: [docs/SECURITY.md](docs/SECURITY.md).

## Deployment

Target: DigitalOcean App Platform from GitHub, using [.do/app.yaml](.do/app.yaml):

```
GitHub push ─► GitHub Actions (lint, types, tests, Postgres integration, image build, smoke)
           ─► App Platform builds the Dockerfile
           ─► PRE_DEPLOY job: alembic upgrade head
           ─► api service starts; /health/ready gates traffic
           ─► python scripts/smoke_test.py https://<app>.ondigitalocean.app
```

Step-by-step, secrets, rollback and troubleshooting: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
Runbooks: [docs/RUNBOOK.md](docs/RUNBOOK.md).

## Design Decisions

Recorded as lightweight ADRs in [docs/DECISIONS.md](docs/DECISIONS.md). Highlights:

- **Stateless HMAC links, not a `links` table.** The signature carries everything needed to
  verify; the database is consulted only for the file's current state. Restarts and horizontal
  scaling are free. Trade-off: individual links cannot be revoked before expiry (deleting the
  file revokes all of them). See ADR-004.
- **Synchronous processing.** Upload, hash and persist complete within one request; no queue
  until a workload (virus scanning, thumbnails) needs it. ADR-006.
- **Static API keys for owner identity.** The brief requires a user id per file but defines no
  identity provider; keys map to user ids in configuration. Swappable for JWT/OIDC at one
  dependency. ADR-005.
- **Local filesystem storage behind a port.** Required by the brief; isolated behind
  `FileStorage` so object storage is a new adapter, not a rewrite. ADR-003.

## Assumptions

Listed in [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md). The load-bearing ones: files are at most
100 MiB, a single API instance with a persistent volume is acceptable for version 1, link holders
are trusted for the TTL they were granted, and server clocks are NTP-synchronised.

## Known Limitations

Honest list in [docs/KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md): no per-link revocation,
local disk means one instance (and ephemeral disk on App Platform without a volume), no rate
limiting, no virus scanning, no HTTP range requests, API-key auth only.

## Scaling Strategy

Stages, bottlenecks first, then technology: [docs/SCALING.md](docs/SCALING.md). Short version:
move bytes to object storage (adapter swap), run N stateless replicas, add read replicas for
audit queries, introduce a queue only for asynchronous post-processing.

## Improvements With More Time

Prioritised in [docs/REVIEW_NOTES.md](docs/REVIEW_NOTES.md): object storage adapter with
presigned direct downloads, per-link revocation table, rate limiting at the edge, range requests,
malware scanning worker, OIDC, retention policies and audit export.

## Repository Layout

```
app/
  main.py                application factory, middleware order, OpenAPI post-processing
  api/                   HTTP layer: routers, dependencies, error mapping, ASGI middleware
  application/           use cases: commands (upload, link, delete), queries, download service
  domain/files/          entities, ports (Protocols), pure services, state machine, exceptions
  infrastructure/        SQLAlchemy models/repositories, engine/session, local storage adapter
  core/                  config, security primitives, logging, telemetry, lifecycle
  schemas/               Pydantic request/response models
  health/                liveness and readiness probes
migrations/              Alembic environment and versions
tests/                   unit · api · integration · contract · e2e · performance · fixtures
scripts/                 smoke_test.py · wait_for_db.py · seed.py · export_openapi.py
docs/                    architecture, decisions, operations, security, testing, runbooks…
presentation/            architecture review guide, demo script, behavioural notes
openapi/openapi.yaml     committed API contract (checked in CI)
.do/app.yaml             DigitalOcean App Platform spec
.github/workflows/       ci.yml · security.yml
Dockerfile · docker-compose.yml · Makefile · pyproject.toml · alembic.ini · .env.example
```

License: [MIT](LICENSE). Changes: [CHANGELOG.md](CHANGELOG.md).
