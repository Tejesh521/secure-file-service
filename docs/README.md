# Secure File Service: Master Review Document

Single consolidated reference for reviewing this codebase. It merges the fifteen topic files in
`docs/` into one document organised by review concern, and points at the code and tests that
back each claim. The topic files remain for depth; the index at the end says what each one adds.

**How to use it for a code review.** Section 1 gives the reading order for the code. Section 2
is a checklist of the invariants the code must uphold, each with the file that enforces it and
the test that proves it. Sections 3 onwards are the condensed design, operations and decision
material you may need while reading.

---

## Contents

1. [Reviewer's guide: reading order](#1-reviewers-guide-reading-order)
2. [Review checklist: invariants, code, tests](#2-review-checklist-invariants-code-tests)
3. [What the service does](#3-what-the-service-does)
4. [Architecture](#4-architecture)
5. [Request flows](#5-request-flows)
6. [Domain model and persistence](#6-domain-model-and-persistence)
7. [API surface](#7-api-surface)
8. [Security](#8-security)
9. [Error handling and failure modes](#9-error-handling-and-failure-modes)
10. [Observability and operations](#10-observability-and-operations)
11. [Testing](#11-testing)
12. [Deployment](#12-deployment)
13. [Decisions and trade-offs](#13-decisions-and-trade-offs)
14. [Assumptions](#14-assumptions)
15. [Known limitations](#15-known-limitations)
16. [Scaling path](#16-scaling-path)
17. [Review talking points](#17-review-talking-points)
18. [Index of source documents](#18-index-of-source-documents)

---

## 1. Reviewer's guide: reading order

The application is about 2,800 lines of Python under `app/`. Dependency direction is
`api → application → domain ← infrastructure`; the domain imports nothing from FastAPI or
SQLAlchemy. This order follows the security-critical path first, then the plumbing.

| Step | File | Lines | What to look for |
|---|---|---|---|
| 1 | `app/main.py` | 101 | App factory, middleware order, router mounting, lifespan |
| 2 | `app/core/config.py` | 172 | Settings, `SIGNING_KEYS`/`API_KEYS` parsing, production safety rails, `normalize_database_url` |
| 3 | `app/core/security.py` | 112 | `UrlSigner` (HMAC-SHA256, key ring, `compare_digest`), `ApiKeyAuthenticator` (constant time) |
| 4 | `app/application/services/download_service.py` | 106 | The decision chain: signature → expiry → metadata → status → bytes → audit |
| 5 | `app/application/commands/upload_file.py` | 134 | Bytes-before-metadata, idempotency replay/conflict, race fallback, cleanup on every failure path |
| 6 | `app/application/commands/create_signed_link.py` | 99 | TTL validation, ownership check, audit row in the same transaction as the response |
| 7 | `app/application/commands/delete_file.py` | 74 | Soft delete, commit, then unlink; unlink failure logged not raised |
| 8 | `app/infrastructure/storage/local.py` | 112 | Random sharded keys, temp file + `os.replace`, streaming size cap, traversal guard, `0700` |
| 9 | `app/api/middleware.py` | 219 | Raw ASGI middleware: request id, body size limit (header and streaming), access log + metrics |
| 10 | `app/api/errors.py` | 121 | One error envelope; `DOMAIN_STATUS` map; 5xx message hiding; `OperationalError` → 503 |
| 11 | `app/api/dependencies.py` | 138 | Per-request wiring of settings, session, signer, storage, metrics; `get_current_user` |
| 12 | `app/api/v1/files.py`, `app/api/v1/downloads.py` | 163, 50 | Route definitions, `def` not `async def`, response headers on download |
| 13 | `app/domain/files/services.py`, `state.py`, `ports.py`, `entities.py`, `exceptions.py` | 72, 28, 49, 84, 63 | Filename sanitisation, content-type shape, TTL/expiry math, `available → deleted` state machine, Protocol ports |
| 14 | `app/infrastructure/repositories/*`, `app/infrastructure/database/models/*`, `migrations/versions/0001_initial.py` | | Unique constraints, composite indexes, FK cascade, UTC normalisation, explicit flush ordering |
| 15 | `app/core/lifecycle.py`, `app/core/logging.py`, `app/health/*` | 82, 101 | Startup order, readiness flag, SIGTERM handling, JSON formatter, probes |
| 16 | `app/schemas/*` | | Pydantic models with `extra="forbid"`, header and query patterns |

Tests to read alongside: `tests/unit/test_download_service.py`, `tests/unit/test_upload_handler.py`,
`tests/unit/test_security.py`, `tests/unit/test_local_storage.py`, `tests/api/test_links_and_download.py`,
`tests/integration/test_api_postgres.py`. In-memory fakes for the ports live in `tests/fixtures/fakes.py`.

---

## 2. Review checklist: invariants, code, tests

Each row is a property the design depends on. Verify the code enforces it and the named test
proves it.

| # | Invariant | Enforced in | Proven by |
|---|---|---|---|
| I1 | Bytes are never addressable by a client-controlled path. Storage keys are server-generated UUIDs; `path_for` refuses keys that resolve outside the root. | `infrastructure/storage/local.py` | `unit/test_local_storage.py` (traversal keys refused), `unit/test_domain_services.py` (filename sanitisation) |
| I2 | A link's authority is exactly the signed tuple `(file_id, link_id, exp)` under a server secret. Changing any field breaks the signature. | `core/security.py` | `unit/test_security.py` (every field change breaks sig), `api/test_links_and_download.py` (extended `exp`, link for A cannot fetch B) |
| I3 | Bad signatures never touch the database. Signature check is first in the download chain. | `application/services/download_service.py` | `unit/test_download_service.py` (bad signature never touches DB) |
| I4 | Download fails closed: signature → expiry → metadata → status → bytes present. A deleted file returns 410 even with a valid link. | same | `unit/test_download_service.py`, `api/test_links_and_download.py` |
| I5 | Links survive restarts and are valid on any instance with the key ring. No link state beyond the audit row. | `core/security.py` (stateless signer) | `api/test_links_and_download.py` (restart survival), `unit/test_security.py` (fresh signer verifies old links) |
| I6 | Key rotation: new links use the active key; old links verify against any key in the ring; unknown `kid` → 403. | `core/security.py`, `core/config.py` (active key must be in ring) | `unit/test_security.py`, `unit/test_config.py` |
| I7 | Every link generation writes a `link.generated` audit row in the same transaction; if the insert fails, no link is returned. | `application/commands/create_signed_link.py` | `unit/test_create_link_handler.py` |
| I8 | Bytes before metadata on upload; every persistence failure path deletes the orphaned bytes. Metadata without bytes is never created by the service. | `application/commands/upload_file.py` | `unit/test_upload_handler.py` (persistence failure removes bytes) |
| I9 | Idempotency is scoped `(owner_id, key)`, arbitrated by a unique constraint, not by the lookup. Same content → 200 replay; different content → 409; concurrent race → loser rolls back, re-reads, replays. | `upload_file.py`, `migrations/versions/0001_initial.py` | `unit/test_upload_handler.py` (simulated race), `integration/test_api_postgres.py` (16 concurrent uploads → one row, one event) |
| I10 | Owner scoping is in SQL; a missing file and a foreign owner's file return an identical 404. | `infrastructure/repositories/file_repository.py` (`get_for_owner`) | `api/test_files.py` (identical 404 across all routes) |
| I11 | API key comparison is constant time across all configured keys. | `core/security.py` | `unit/test_security.py` |
| I12 | Upload size is enforced by `Content-Length` and again while streaming; a lying client is cut off and the temp file removed. | `api/middleware.py`, `storage/local.py` | `api/test_auth_and_errors.py` (both paths), `unit/test_local_storage.py` (endless reader) |
| I13 | Partially written objects are never visible under their final key (temp file + atomic rename). | `storage/local.py` | `unit/test_local_storage.py` (no partial files after failure) |
| I14 | Soft delete commits before unlinking; unlink failure is logged, not raised; all outstanding links return 410 from the commit onwards. | `application/commands/delete_file.py` | `unit/test_delete_handler.py`, `api/test_files.py` |
| I15 | The `files` row is flushed before dependent audit inserts (PostgreSQL FK ordering; SQLite hid this bug). | `upload_file.py` | `integration/test_api_postgres.py`, `integration/test_repositories.py` |
| I16 | One error envelope everywhere; 5xx hides the message and returns only `request_id`; `OperationalError` → 503, other `SQLAlchemyError` → 500. | `api/errors.py` | `api/test_auth_and_errors.py`, `contract/test_openapi_contract.py` |
| I17 | Request id is validated (`^[A-Za-z0-9_.:-]{8,128}$`), preserved when valid, replaced when invalid, present on every response, error body and audit row. | `api/middleware.py` | `unit/test_logging_and_middleware.py`, `api/test_auth_and_errors.py` |
| I18 | Metrics use route templates, never raw paths; unmatched paths collapse to `unmatched`. | `api/middleware.py` | `api/test_health_and_metrics.py` |
| I19 | Liveness never checks dependencies. Readiness checks startup, `SELECT 1` and storage writability, and flips to 503 on SIGTERM before draining. | `health/live.py`, `health/ready.py`, `core/lifecycle.py` | `api/test_health_and_metrics.py` |
| I20 | Production refuses to start with development secrets, a signing key under 32 bytes, or a non-HTTPS `PUBLIC_BASE_URL`. | `core/config.py` | `unit/test_config.py` |
| I21 | Filenames and content types are display metadata only: NFC-normalised, basename only, control chars removed, ≤ 255 chars; `Content-Disposition` uses ASCII fallback plus RFC 5987 so header injection is impossible. | `domain/files/services.py` | `unit/test_domain_services.py` |
| I22 | State machine is `available → deleted` only; deleted is terminal. | `domain/files/state.py` | `unit/test_state.py` |
| I23 | Committed OpenAPI snapshot equals `app.openapi()`; every non-health operation declares error responses using `ErrorResponse`. | `openapi/openapi.yaml`, `scripts/export_openapi.py` | `contract/test_openapi_contract.py` |
| I24 | Models and migrations do not drift. | `migrations/` | `integration/test_migrations.py`, `make migration-check` in CI |
| I25 | Secrets, keys, signatures, `DATABASE_URL` and file contents are never logged. | `core/logging.py`, handlers | manual review of log calls |

---

## 3. What the service does

Users store private files on a server and hand out temporary download links to people with no
account. Requirements from the brief and where each is met:

| # | Requirement | Implementation |
|---|---|---|
| F1 | Upload to a non-public local directory, associated with a user id | `UploadFileHandler` streams into `STORAGE_ROOT/.tmp`, renames atomically into `STORAGE_ROOT/<2-char shard>/<uuid>`; `files.owner_id` comes from the authenticated API key |
| F2 | Signer endpoint with file id + TTL, valid across restarts | `CreateSignedLinkHandler` + `UrlSigner` (HMAC-SHA256, secret from config, key ring) |
| F3 | Public endpoint validating signature and expiry, serving the file | `DownloadService.resolve` then Starlette `FileResponse` |
| F4 | Owners query file status; audit event on every link generation | `GET /v1/files…` queries; `audit_events` row `link.generated` written in the same transaction as the link is issued |

Non-functional: production-ready error handling, validation, tests, CI, documentation.

Quality attributes in priority order, because they drove the trade-offs:

1. **Correctness of access control**: no reading a file without a valid signature or ownership; no forging, extending or transplanting a signature.
2. **Durability of metadata**: a file the API says exists must exist; bytes without metadata are sweepable garbage, metadata without bytes is a served error logged loudly.
3. **Operability**: every failure diagnosable from a request id; probes reflect reality; graceful shutdown.
4. **Simplicity**: one process, one database, one disk; extension points where growth is likely.
5. **Performance**: uploads stream in 64 KiB chunks; the download hot path is one HMAC, one primary-key lookup, one insert and one `sendfile`.

---

## 4. Architecture

![Architecture diagram](diagrams/architecture.svg)

```
                     ┌──────────────────────────┐
                     │  DigitalOcean App Platform│
                     │  ingress · TLS · health   │
                     └─────────────┬─────────────┘
                                   │
      ┌────────────────────────────▼────────────────────────────┐
      │ FastAPI service (stateless)                              │
      │  api/          middleware (request id, body limit,       │
      │                access log + metrics), routers, error map │
      │  application/  UploadFileHandler · CreateSignedLink…     │
      │                DeleteFileHandler · DownloadService ·     │
      │                queries                                   │
      │  domain/       entities · ports · services · state       │
      │  infrastructure/ SQLAlchemy repos · LocalFileStorage     │
      │  core/         config · UrlSigner · logging · metrics    │
      └───────────┬──────────────────────────────┬───────────────┘
                  │                              │
        ┌─────────▼─────────┐          ┌─────────▼──────────┐
        │ PostgreSQL         │          │ Private storage root│
        │ files              │          │ 0700, random keys,  │
        │ audit_events       │          │ never served by path│
        └────────────────────┘          └─────────────────────┘
        ┌──────────────────────────────────────────────────────┐
        │ Operational plane: JSON logs · /metrics · /health/*  │
        │ X-Request-ID on every response and audit row         │
        └──────────────────────────────────────────────────────┘
```

**Layers.** `api` owns HTTP concerns. `application` handlers own use cases and the transaction
boundary (one `commit` per use case). `domain` holds entities, `Protocol` ports (`FileRepository`,
`AuditRepository`, `FileStorage`, `Clock`), pure services and the state machine.
`infrastructure` implements the ports with SQLAlchemy 2.x and the local filesystem. Unit tests
substitute in-memory fakes for the ports.

**Processing model.** Synchronous and request-scoped. Endpoint functions are `def`, not
`async def`, so FastAPI runs them in the thread pool, which suits blocking file and database I/O
with psycopg 3 sync. Middleware is written as raw ASGI callables rather than `BaseHTTPMiddleware`
so streaming responses are not buffered. No queue: every upload step completes in tens of
milliseconds at the target sizes. The seam for a future queue is a transactional outbox row
written in the same commit as the `files` row.

**Components.**

| Component | Responsibility | File |
|---|---|---|
| `RequestIdMiddleware` | Correlation id in scope, ContextVar and response header | `app/api/middleware.py` |
| `BodySizeLimitMiddleware` | Reject by `Content-Length` early; enforce while streaming; 413 | same |
| `AccessLogMiddleware` | One structured line plus counters/histograms per request; template routes | same |
| Exception handlers | Map domain, validation, HTTP, DB and unexpected errors to one envelope | `app/api/errors.py` |
| Dependencies | Wire settings, session, signer, storage, metrics per request | `app/api/dependencies.py` |
| `UrlSigner` | Sign/verify with key ring; constant-time compare; stateless, no clock or I/O | `app/core/security.py` |
| `ApiKeyAuthenticator` | Static key → user id, constant time over all keys | same |
| Handlers / services | Use cases; own the transaction boundary | `app/application/**` |
| Domain services | Filename sanitisation, content-type shape, TTL/expiry math | `app/domain/files/services.py` |
| State machine | `available → deleted`; deleted is terminal | `app/domain/files/state.py` |
| Repositories | SQLAlchemy 2.x, map models ↔ entities, UTC-aware datetimes | `app/infrastructure/repositories/` |
| `LocalFileStorage` | Random sharded keys, temp file + atomic rename, streaming size cap, traversal guard | `app/infrastructure/storage/local.py` |
| Lifecycle | Startup order, readiness flag, graceful shutdown | `app/core/lifecycle.py` |

---

## 5. Request flows

### 5.1 Upload

```
client ──multipart──► BodySizeLimitMiddleware (Content-Length ≤ MAX_UPLOAD_BYTES + 64 KiB, enforced again while streaming)
       ──► RequestIdMiddleware (preserve valid X-Request-ID or mint req_<uuid>)
       ──► get_current_user: X-API-Key → owner_id (constant time), else 401
       ──► UploadFileHandler.execute
             1. storage.write(stream): .tmp/<uuid>.part, sha256 while writing, abort > max,
                fsync, os.replace → <shard>/<key>          (EmptyUpload → 422, TooLarge → 413)
             2. Idempotency-Key present? find_by_idempotency_key(owner, key)
                  hit + same sha256/size → delete new bytes, return existing (200)
                  hit + different content → delete new bytes, 409 IDEMPOTENCY_KEY_REUSED
             3. INSERT files (flush) + INSERT audit_events(file.uploaded); COMMIT
                  IntegrityError (concurrent same key) → rollback, re-lookup, replay
                  any other failure → rollback, delete bytes, re-raise (500/503)
       ◄── 201 Created, Location: /v1/files/{id}
```

Bytes before metadata, deliberately: the content hash is needed to decide whether an idempotent
replay carries the same payload, and orphaned bytes are harmless whereas metadata pointing at
nothing would be a serving error.

### 5.2 Link generation

```
POST /v1/files/{id}/links {"ttl_seconds": 600}
  ──► auth → owner_id
  ──► validate_ttl(ttl, default=DEFAULT_LINK_TTL, max=MAX_LINK_TTL)   (→ 422 INVALID_TTL)
  ──► files.get_for_owner(id, owner)         (None → 404; deleted → 410 FILE_UNAVAILABLE)
  ──► exp = now + ttl ; link_id = uuid4
  ──► sig = HMAC-SHA256(key[active], "v1\n{file_id}\n{link_id}\n{exp}")  → base64url, 43 chars
  ──► INSERT audit_events(link.generated, actor, link_id, expires_at, ttl, key_id, request_id, ip); COMMIT
  ◄── 201 {"url": "{PUBLIC_BASE_URL}/v1/download/{id}?exp=…&lid=…&kid=…&sig=…", "expires_at", …}
```

There is no state about the link other than the audit row, so any instance holding the key ring
can verify the URL.

### 5.3 Download

```
GET /v1/download/{id}?exp=&lid=&kid=&sig=      (no credentials)
  ──► query params shape-validated (exp int ≥ 0, sig matches ^[A-Za-z0-9_-]{43}$) → 422
  ──► DownloadService.resolve, in this order, failing closed:
        signer.verify(file_id, lid, exp, kid, sig)      ✗ → 403 LINK_SIGNATURE_INVALID (no DB hit)
        is_expired(exp, clock.now())                    ✗ → 410 LINK_EXPIRED
        files.get(id)                                   ✗ → 404 FILE_NOT_FOUND
        record.is_available                             ✗ → 410 FILE_UNAVAILABLE
        storage.exists(storage_key)                     ✗ → 500 STORAGE_INCONSISTENT (logged ERROR)
        INSERT audit_events(file.downloaded, lid, request_id, ip); COMMIT
  ◄── 200 FileResponse, Content-Disposition: attachment, Cache-Control: private, no-store,
      X-Content-Type-Options: nosniff, ETag: "<sha256>"
```

Signature first so garbage or brute-force traffic costs one HMAC and no I/O. Expiry is inside
the signed message, so changing `exp` in the URL invalidates `sig`. `now == exp` counts as expired.

### 5.4 Delete

Soft delete: set `status = deleted` and `deleted_at`, write the audit row, **commit**, then remove
the bytes. If the unlink fails the error is logged and the request still succeeds; metadata is
authoritative and a reconciliation sweep can remove leftovers. Idempotent: deleting again is 204.

---

## 6. Domain model and persistence

```
FileRecord                      AuditEvent                    SignedLink (value, never stored)
  id (uuid)                       id                            link_id
  owner_id                        file_id ─┐                    file_id
  original_filename (sanitised)   event_type: file.uploaded |   url
  content_type (normalised)         link.generated |            expires_at
  size_bytes, sha256                file.downloaded |           key_id
  storage_key (server-generated)    file.deleted                ttl_seconds
  status: available | deleted     actor_id (null for downloads)
  idempotency_key?                link_id?, expires_at?
  created_at, updated_at,         request_id?, client_ip?
  deleted_at?                     metadata (JSONB)
```

PostgreSQL via SQLAlchemy 2.x with explicit Alembic migrations (never `create_all` in
production). Constraints do the arbitration application code cannot do safely under concurrency:
`UNIQUE(owner_id, idempotency_key)` and `UNIQUE(storage_key)`. Both hot queries have a covering
composite index. `audit_events.file_id` cascades on delete. All timestamps are `TIMESTAMPTZ`;
repositories normalise to UTC-aware `datetime`s even on SQLite. Engine settings: `pool_pre_ping`,
`pool_size`/`max_overflow` from config, `connect_timeout` and `statement_timeout` (5 s) per
connection so no query can hang a worker indefinitely.

---

## 7. API surface

Base path `/v1`. Interactive docs at `/docs`. Spec at `/openapi.json` and `openapi/openapi.yaml`
(regenerate with `make openapi`; CI fails if stale).

| Method and path | Auth | Success | Purpose |
|---|---|---|---|
| `POST /v1/files` | `X-API-Key` | 201 (200 on idempotent replay) | Upload; multipart part `file`; optional `Idempotency-Key` (1–255 chars `[A-Za-z0-9_.:-]`) |
| `GET /v1/files` | `X-API-Key` | 200 | List own files, newest first, `limit` 1–100 (default 20), `offset` 0–1,000,000 |
| `GET /v1/files/{id}` | `X-API-Key` | 200 | Metadata; deleted files still returned with `status: deleted` |
| `DELETE /v1/files/{id}` | `X-API-Key` | 204 | Soft delete plus byte removal; idempotent |
| `POST /v1/files/{id}/links` | `X-API-Key` | 201 | Mint signed link; body `{"ttl_seconds"}` optional, default 3600, extra fields rejected |
| `GET /v1/files/{id}/audit` | `X-API-Key` | 200 | Paginated audit trail for the owner |
| `GET /v1/download/{id}?exp&lid&kid&sig` | none | 200 | Public download; the signature is the credential |
| `GET /health/live` | none | 200 | Process is up; never checks dependencies |
| `GET /health/ready` | none | 200 / 503 | Startup done, `SELECT 1` ok, storage writable; per-check detail |
| `GET /metrics` | none | 200 | Prometheus text format |

**Signed URL parameters.** `exp` Unix epoch seconds UTC; `lid` link id (UUID); `kid` signing key
id; `sig` = `base64url(HMAC-SHA256(secret[kid], "v1\n{file_id}\n{lid}\n{exp}"))` without padding,
43 chars.

**Correlation.** Send `X-Request-ID` (8–128 chars of `[A-Za-z0-9_.:-]`) to have it preserved;
otherwise one is generated. Returned on every response, in every error body and audit event.

**Error envelope.** Every non-2xx response:

```json
{"error": {"code": "VALIDATION_ERROR", "message": "Request validation failed.",
           "details": [{"field": "query.limit", "reason": "Input should be greater than or equal to 1"}],
           "request_id": "req_2f8c…"}}
```

| HTTP | `code` | When |
|---|---|---|
| 400 | `BAD_REQUEST` | Malformed request outside validation |
| 401 | `UNAUTHORIZED` | Missing or unknown `X-API-Key` (`WWW-Authenticate: ApiKey`) |
| 403 | `LINK_SIGNATURE_INVALID` | Signature does not verify, or unknown `kid` |
| 404 | `FILE_NOT_FOUND` | No such file **or** file belongs to another owner (indistinguishable on purpose) |
| 404 | `NOT_FOUND` | Unknown route |
| 405 | `METHOD_NOT_ALLOWED` | |
| 409 | `IDEMPOTENCY_KEY_REUSED` | Same key, different content |
| 410 | `LINK_EXPIRED` | `exp` is in the past |
| 410 | `FILE_UNAVAILABLE` | File was deleted (link generation or download) |
| 413 | `PAYLOAD_TOO_LARGE` | Body exceeds `MAX_UPLOAD_BYTES` (uploads) or `MAX_REQUEST_BODY_BYTES` (others) |
| 422 | `VALIDATION_ERROR` | Shape/type/range failures; see `details` |
| 422 | `EMPTY_UPLOAD` | Zero-byte file |
| 422 | `INVALID_TTL` | `ttl_seconds` outside `1..MAX_LINK_TTL_SECONDS` |
| 500 | `INTERNAL_ERROR` | Unexpected failure; message hidden, use `request_id` |
| 500 | `STORAGE_INCONSISTENT` | Metadata exists but bytes are missing (alert-worthy) |
| 503 | `SERVICE_UNAVAILABLE` | Database unreachable; safe to retry |

Download response headers: `Content-Type` (stored), `Content-Length`,
`Content-Disposition: attachment; filename="…"; filename*=UTF-8''…`, `Cache-Control: private, no-store`,
`X-Content-Type-Options: nosniff`, `ETag: "<sha256>"`, `Accept-Ranges: bytes`.

Audit event types: `file.uploaded`, `link.generated`, `file.downloaded`, `file.deleted`.

---

## 8. Security

**Threat model.** Assets: file bytes (confidentiality), metadata and audit trail (integrity),
availability. Actors: anonymous users holding or guessing URLs; authenticated owners acting on
other owners' files; a compromised client with a leaked API key; an attacker with read access to
configuration. Out of scope for v1: malware in uploaded content, DDoS (edge concern), insider
with database write access.

**The two invariants that matter most.** Bytes are never addressable by client-controlled paths,
and a link's authority is exactly the tuple it was signed over (file, link id, expiry) with the
server's secret.

**Controls by area.**

- *Storage*: server-generated UUID keys; client names affect display only; `path_for` refuses keys outside the root; root `0700`; no static file serving; temp file + atomic rename; size enforced twice.
- *Signed links*: HMAC-SHA256 with `hmac.compare_digest`; expiry and file id inside the signed message; key ring with `kid` for rotation; state checked after signature so deleted files give 410; production requires secrets of at least 32 bytes and refuses the development key.
- *Authentication and authorisation*: `X-API-Key` compared in constant time across all keys; every owner query scoped by `owner_id` in SQL; missing and foreign files return identical 404s; download endpoint is credential-free by design.
- *Input validation*: Pydantic `extra="forbid"`; TTL range; pagination caps; header patterns for `Idempotency-Key` and `X-Request-ID`; `sig` must be exactly 43 URL-safe chars; filenames NFC-normalised, basename only, control chars removed, length-capped; `Content-Disposition` built with ASCII fallback and RFC 5987 encoding; content types validated against token/token or replaced with `application/octet-stream`.
- *Transport and headers*: TLS at the platform edge; production refuses a non-HTTPS `PUBLIC_BASE_URL`; `TrustedHostMiddleware` when `TRUSTED_HOSTS` is set; CORS off unless configured; downloads served as `attachment`, `nosniff`, `no-store`.
- *Errors and logging*: 5xx carries only a request id; secrets, keys, signatures and connection strings never logged.
- *Supply chain and runtime*: multi-stage image on `ubuntu:24.04` LTS with the distribution's Python 3.12; runtime stage has only the interpreter, CA certificates and curl; security updates at build; non-root uid 10001. CI runs `pip-audit --strict`, Ruff `S` rules, Trivy (HIGH/CRITICAL) and gitleaks.

**Residual risks.**

| Risk | Status | Mitigation / next step |
|---|---|---|
| Link shared beyond intended recipient | inherent to capability URLs | short TTLs (default 1 h, max 7 d); delete file to revoke; per-link revocation table planned |
| API key leakage | bearer secret | TLS only; rotate via config; move to OIDC tokens with expiry |
| Brute-forcing signatures | 2^256 space | infeasible; monitor `download_failures_total{reason="signature"}` |
| Malicious content served to a browser | attachment + nosniff | scanning worker before `status=available` |
| No rate limiting | v1 gap | edge rate limits or per-key limiter |
| Disk exhaustion by one owner | per-file cap only | per-owner quota |
| Ephemeral disk on App Platform | documented | object storage adapter |

**Why static API keys.** The brief requires a user id per file but defines no identity provider,
registration or authorisation model beyond "owner". Static keys satisfy that with the least
invented machinery, and the `get_current_user` dependency is the single place that would validate
OIDC tokens instead; nothing else changes.

---

## 9. Error handling and failure modes

One envelope for everything (section 7). Domain exceptions carry a stable `code` and are mapped
to HTTP statuses in one table (`DOMAIN_STATUS`). Anything ≥ 500 hides its message, logs the stack
trace with the request id, and returns only the id. `OperationalError` is a 503 so clients and
load balancers retry; other `SQLAlchemyError`s are 500. Principles: fail closed on the download
path; never leave metadata without bytes; readiness reflects dependency health; every failure
increments a labelled counter so dashboards show *why* things fail.

| Failure | Behaviour | Detection | Recovery |
|---|---|---|---|
| Missing `file` part / malformed multipart | 422 with field detail | 422 counter | client |
| Empty file | 422 `EMPTY_UPLOAD`; nothing persisted | `upload_failures_total{reason="EmptyUpload"}` | client |
| Oversized, declared | 413 before reading body | 413 counter | client |
| Oversized, undeclared (chunked) | cut off while streaming; temp removed; 413 | `reason="UploadTooLarge"` | client |
| Duplicate, same key + content | 200 existing record; new bytes discarded | log line | none |
| Duplicate, same key, different content | 409 | `reason="idempotency_conflict"` | new key |
| Concurrent duplicates | unique constraint wins; loser rolls back, re-reads, replays | integration test | none |
| DB down during upload | bytes written then removed; 503; readiness 503 | ERROR log; readiness | retry with same key |
| DB down during link generation | 503; no link, no audit row (atomic) | same | retry |
| DB down during download | 503; if audit insert fails after checks, no bytes served | same | retry |
| Storage root unwritable | startup fails fast or readiness 503 `storage: unavailable` | readiness | fix mount/permissions |
| Bytes missing for available row | 500 `STORAGE_INCONSISTENT`; ERROR with `file_id`, `storage_key` | `reason="storage_missing"` → page | restore or mark deleted |
| Orphaned bytes after crash between write and commit | unreachable object | disk usage vs `size_bytes` sum | reconciliation sweep |
| Tampered sig / wrong file id / wrong kid | 403; no DB hit | `reason="signature"` | alert on spike |
| Expired link | 410 | `reason="expired"` | owner mints new link |
| Link for deleted file | 410 `FILE_UNAVAILABLE` | `reason="unavailable"` | intended |
| Signing key removed from ring | all links signed with it → 403 | spike after deploy | re-add if unintended |
| Clock skew | links expire slightly early/late | none (NTP assumed) | NTP; tolerance if needed |
| Unexpected exception | 500 with request id only; stack logged | 5xx ratio | investigate by id |
| Slow query | `statement_timeout` 5 s → 503 | `db_query_duration_seconds` | index/plan review |
| Pool exhaustion | wait `pool_timeout` then 503 | `db_pool_connections` | raise pool / PgBouncer |
| SIGTERM during requests | readiness 503 at once; in-flight finish within `SHUTDOWN_TIMEOUT_SECONDS`; pool disposed | `shutdown started` log | none |
| Bad production config | refuses to start with clear `ValueError` | failed deploy | fix env |
| Bad migration | PRE_DEPLOY job fails; old version keeps serving | deploy alert | fix forward |
| Disk full | `OSError` → temp removed → 500; readiness may pass until probe write fails | disk metrics | free space; quotas |

**Lifecycle.**

```
start:  configure logging → validate settings (fail fast) → engine → storage.ensure_ready()
        (mkdir 0700, write probe) → tracing (optional) → ready=true → "service started"
SIGTERM: ready=false → uvicorn stops accepting → in-flight finish (≤ SHUTDOWN_TIMEOUT_SECONDS)
        → engine.dispose() → logging.shutdown() → exit
```

---

## 10. Observability and operations

**Logs.** One JSON object per line to stdout: `timestamp`, `level`, `logger`, `service`,
`environment`, `message`, `request_id`, `user_id`, plus per-event extras (`file_id`, `link_id`,
`ttl_seconds`, `status_code`, `duration_ms`, `client_ip`, `route`). Health and metrics requests
are logged only when they fail. `LOG_FORMAT=console` for local work. The request id lives in a
`ContextVar` so every line during a request carries it.

**Metrics** (Prometheus at `/metrics`; route labels are templates, so cardinality is bounded by
routes × methods × status codes).

| Metric | Type | Labels | Use |
|---|---|---|---|
| `http_requests_total` | counter | method, route, status_code | traffic, error rate |
| `http_request_duration_seconds` | histogram | method, route | latency SLO |
| `http_requests_in_progress` | gauge | method | saturation |
| `files_uploaded_total`, `upload_bytes_total`, `links_generated_total`, `downloads_total`, `files_deleted_total` | counter | | business volume |
| `upload_failures_total` | counter | reason | `UploadTooLarge`, `EmptyUpload`, `idempotency_conflict`, `persistence_error` |
| `download_failures_total` | counter | reason | `signature`, `expired`, `not_found`, `unavailable`, `storage_missing` |
| `db_query_duration_seconds` | histogram | | DB latency |
| `db_pool_connections` | gauge | | pool saturation |

**Tracing.** `OTEL_ENABLED=true`, `OTEL_EXPORTER_OTLP_ENDPOINT`, and the `otel` extra. FastAPI
and SQLAlchemy instrumented; health/metrics excluded. Optional so the service never fails to
start because a collector is unreachable.

**Alerts.**

| Alert | Condition | Severity |
|---|---|---|
| High 5xx | ratio > 1% for 5 min | page |
| Readiness failing | non-200 for 2 min | page |
| Storage inconsistency | `download_failures_total{reason="storage_missing"}` > 0 in 10 min | page (data integrity) |
| Latency | p95 `POST /v1/files` > 2 s or download > 500 ms for 10 min | ticket |
| Pool saturation | `db_pool_connections` ≥ pool_size for 5 min | ticket |
| Signature failures spike | rate 10× baseline | ticket (probing) |
| Deployment failed | App Platform alert | page |

**SLOs.** Availability (non-5xx, 4xx excluded) 99.9% monthly. Download p95 to first byte < 250 ms.
Link generation p95 < 150 ms. Upload p95 (≤ 10 MiB) < 2 s. Unexpected 5xx < 0.1%.

**Procedures.**

- *Rotate signing key (planned)*: `make secret`; set `SIGNING_KEYS=k2:<new>,k1:<old>` and `SIGNING_ACTIVE_KEY_ID=k2`; redeploy; after `MAX_LINK_TTL_SECONDS` (7 days) remove `k1`; redeploy.
- *Rotate signing key (compromise)*: same, but remove `k1` immediately, accepting every outstanding link dies.
- *Rotate an API key*: add the new pair, redeploy, remove the old pair, redeploy.
- *Revoke all links for a file*: `DELETE /v1/files/{id}` as owner; emergency SQL `UPDATE files SET status='deleted', deleted_at=now(), updated_at=now() WHERE id=…`, then remove the object.
- *Reconcile orphaned bytes*: objects under `STORAGE_ROOT/<shard>/` not in `files.storage_key` with `status='available'`, older than one hour, delete.
- *Audit export*: `COPY (SELECT … FROM audit_events WHERE created_at >= …) TO STDOUT CSV`.

**Runbook entries** (symptoms → checks → mitigation → verification) exist for: elevated 5xx,
high latency, database unavailable, storage unavailable, bytes missing, deployment failure, bad
migration, signature failure spike. See [RUNBOOK.md](RUNBOOK.md) for the step lists.

---

## 11. Testing

Tests make three claims with confidence: access control is correct, metadata and bytes never
disagree in a way that serves the wrong thing, and the service behaves predictably when
dependencies fail. Nothing is mocked that can be faked with an in-memory port implementation, and
nothing is faked that the real dependency would answer differently.

```
              e2e (3)            deployed instance, opt-in via E2E_BASE_URL
          contract (4)           OpenAPI snapshot == code; real responses validate against schemas
       integration (13)          PostgreSQL: constraints, cascade, JSONB, migrations, 16-way race
           api (52)              full ASGI stack, in-memory SQLite, temp storage dir
          unit (97)              signer, domain rules, handlers with fakes, storage adapter, config
```

166 tests run locally in about two seconds; 98% line coverage with `fail_under = 85`.

| Layer | Location | What only this layer proves |
|---|---|---|
| Unit | `tests/unit/` | Signer round trips and every tamper case; filename sanitisation; state machine; config rails; handler logic including simulated races and cleanup on failure; storage adapter atomicity, size cap and traversal guard |
| API | `tests/api/` | Full HTTP stack with SQLite (StaticPool), keys `k1` (active) and `k0` (rotated): health/readiness semantics, metrics cardinality, auth and error envelope, request-id handling, upload paths, identical 404 for missing vs foreign, link/download matrix, **restart survival** of a minted link |
| Integration | `tests/integration/` (marker `integration`, needs `TEST_DATABASE_URL`) | Real Alembic migrations up and down; unique constraints raise `IntegrityError` by name; NULL keys do not collide; ordering with stable tiebreak; `JSONB` and FK cascade; expected indexes; `compare_metadata` no drift; **16 concurrent uploads with one key → one row, one event**; `statement_timeout` applied |
| Contract | `tests/contract/` | Committed `openapi/openapi.yaml` equals `app.openapi()`; every non-health operation declares `ErrorResponse` errors; live responses validate against declared JSON Schema 2020-12 |
| E2E | `tests/e2e/` (marker `e2e`, `E2E_BASE_URL`, `E2E_API_KEY`) | Health, full lifecycle including anonymous download and tamper rejection against a deployed stack; `scripts/smoke_test.py` covers the same ground for deploy gates |
| Performance | `tests/performance/` (`RUN_PERFORMANCE=1`) | Regression guard: upload 64 KiB < 250 ms p95, sign < 100 ms, download < 100 ms, listing flat at 300 files |

The integration suite caught a real defect: with SQLite the `files` row and its audit row could
flush in either order; PostgreSQL enforced the FK and rejected the audit insert. The fix (flush
the file row before dependent inserts) is covered.

```bash
make test                # unit + api + contract with coverage
make test-integration    # TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/files_test
make test-e2e E2E_BASE_URL=http://localhost:8080
make test-performance
make verify              # format-check lint typecheck openapi-check test test-integration
```

**Deliberately not tested**: real TLS termination and proxy headers (platform concern); disk-full
beyond the size cap (needs OS-level fault injection); multi-instance on shared storage (v1 is
single-instance); OpenTelemetry export (manual only); load beyond development scale.

**Future**: Hypothesis property tests for `sanitize_filename` and the signer; fault injection for
disk-full and partial write; k6 against staging; mutation testing on the download decision chain.

---

## 12. Deployment

**Local, no Docker.** The development environment has no Docker daemon, so the everyday loop is a
virtualenv plus local PostgreSQL:

```bash
make install && cp .env.example .env
createdb files
make local        # wait_for_db → alembic upgrade head → uvicorn --reload on :8080
make smoke        # scripts/smoke_test.py http://localhost:8080
```

**Image.** Multi-stage on `ubuntu:24.04` LTS with the distribution's Python 3.12. Runtime stage
holds only `python3`, CA certificates and curl; security updates applied at build; copies the
virtualenv plus `app/`, `migrations/`, `alembic.ini` and two scripts; uid 10001; port 8080;
`HEALTHCHECK` on `/health/live`; Uvicorn with `--proxy-headers`, a graceful-shutdown timeout and
no `--reload`. Built and verified only in CI (`make build`, non-root check, container smoke test).

**Pipeline.**

```
GitHub (main) ── push ──► GitHub Actions
   ci.yml:        quality (format · lint · mypy · openapi-check) → unit/api/contract →
                  integration (PostgreSQL service, migration check) → docker (build · non-root · smoke)
   security.yml:  pip-audit · Ruff S rules · Trivy image scan · gitleaks (on push and schedule)
                       │
                       ▼
DigitalOcean App Platform (.do/app.yaml)
   ├── job  "migrate"   PRE_DEPLOY → python scripts/wait_for_db.py && alembic upgrade head
   ├── service "api"    Dockerfile → uvicorn, health check /health/ready, 1 instance
   └── database "files-db"  Managed PostgreSQL 16
```

**Environment variables.**

| Variable | Value |
|---|---|
| `ENVIRONMENT` | `production` (turns on config safety rails) |
| `PUBLIC_BASE_URL` | `${APP_URL}` (must be `https://`) |
| `TRUSTED_HOSTS` | `${APP_DOMAIN}` |
| `DATABASE_URL` | `${files-db.DATABASE_URL}?sslmode=require` (bare `postgresql://` is normalised to the psycopg 3 driver) |
| `STORAGE_ROOT` | `/var/lib/secure-file-service/storage` |
| `SIGNING_KEYS` | SECRET, `k1:<48+ chars>`; generate with `make secret` |
| `API_KEYS` | SECRET, `<key>:<user>,<key>:<user>` |
| `MAX_UPLOAD_BYTES`, `SHUTDOWN_TIMEOUT_SECONDS`, `LOG_LEVEL` | as needed |

**Migrations.** `alembic upgrade head` runs as a PRE_DEPLOY job before the new version receives
traffic. Migrations are expand → migrate → contract, backward compatible with the previous app
version, so a service rollback never needs a schema rollback. `alembic check` in CI prevents drift.

**Rollback.** Service: App Platform → Deployments → Rollback. Configuration: revert env var and
redeploy. Signing key compromise: prepend a new key, make it active, remove the old key when you
are willing to kill links signed with it.

**Storage caveat.** The container filesystem on App Platform is ephemeral: a redeploy or restart
loses files under `STORAGE_ROOT` unless a persistent volume is attached. This is why
`instance_count` is 1 and why the object-storage adapter is the highest-value next step.
Acceptable for a demo, not for real users.

**Troubleshooting.**

| Symptom | Likely cause |
|---|---|
| Startup `ValueError: SIGNING_KEYS must be set…` | Secrets not set; production rails working as intended |
| `doctl apps create` → `400 GitHub user not authenticated` | Account has not authorised GitHub; authorise, or use a `git:` block for a public repo |
| Migrate job `No module named 'psycopg2'` | Bare `postgresql://` URL; fixed by `normalize_database_url` in `app/core/config.py` |
| `/health/ready` 503 `database: unavailable` | `DATABASE_URL`, `sslmode`, database firewall |
| `/health/ready` 503 `storage: unavailable` | `STORAGE_ROOT` not writable by uid 10001 |
| Links point at `http://localhost:8080` | `PUBLIC_BASE_URL` not set |
| 400 `Invalid host header` | `TRUSTED_HOSTS` missing the domain |
| Unexpected 413 | platform request size limit or `MAX_UPLOAD_BYTES` |

---

## 13. Decisions and trade-offs

Each ADR in [DECISIONS.md](DECISIONS.md) records decision, reasoning, alternatives, accepted
trade-off and what changes at scale. Condensed:

| ADR | Decision | Why | Cost accepted | When it would change |
|---|---|---|---|---|
| 001 | FastAPI + Pydantic v2 + Uvicorn | Declarative validation with precise 422s, OpenAPI from code, DI, streaming `UploadFile` | Pydantic coupling in schemas; middleware must be raw ASGI to avoid buffering | Framework matters less than storage/data decisions |
| 002 | PostgreSQL + SQLAlchemy 2.x + Alembic | Constraints arbitrate concurrency; `JSONB`, `TIMESTAMPTZ`; reviewable migrations with drift check | One managed dependency; ORM indirection (repos kept thin) | Read replica, monthly partitioning of `audit_events`, PgBouncer |
| 003 | Local filesystem behind `FileStorage` port | Required by brief; domain never sees paths; adapter swap is one class | Per-instance, ephemeral disk on App Platform → single instance | Before any real users: `SpacesFileStorage`, optional presigned GETs |
| 004 | Stateless HMAC links with key ring, no `links` table | Survive restarts, verify on any replica, microsecond checks, no I/O, rotation via `kid` | No per-link revocation, counters or single-use links; coarse revocation via delete or key removal | First request for "revoke this link": add `revoked_links(lid)` check; URL unchanged |
| 005 | Static API keys | Brief needs a user id but no IdP; single dependency to replace | Rotation is a redeploy; no scopes or per-user limits; bearer secrets over TLS only | As soon as an IdP exists: OIDC subject becomes `owner_id` |
| 006 | Synchronous, no queue | Every step is fast I/O; fewer components to secure and explain | Any slow step added later grows request latency | Malware scan / transcoding: outbox row → queue → workers; `processing`/`quarantined` states |
| 007 | One error envelope, stable codes | Clients branch on `code`; operators paste `request_id`; contract-tested | Slightly more code than `HTTPException` | |
| 008 | Idempotent uploads per owner, DB-arbitrated, hash before decide | Retries after timeouts are common; detects same-key-different-content | Replay still transfers bytes once | Large files: initiate / PUT / complete protocol |
| 009 | Separate liveness and readiness; readiness gates traffic | Do not restart a healthy process for a DB blip; do not route to an instance that cannot write | | |
| 010 | Logs and metrics built in; tracing optional | Always useful and free; tracing needs a backend and must never block startup | | |
| 011 | Ubuntu 24.04 image, built by App Platform from repo | Organisation's LTS standard; one place for CVE handling; one artifact for CI and prod | Larger than slim; builds verified only in CI (no local Docker) | Push scanned images by digest from CI |
| 012 | Soft delete, bytes removed after commit | Metadata is source of truth; lingering bytes are unreachable and reclaimable; audit trail stays attached | Needs a sweep for leftovers | Legal erasure: scheduled purge of deleted rows |

Other trade-offs: SQLite is a test double only, never production (it hid the FK ordering bug);
ORM over raw SQL until audit analytics need hand-written queries; template route labels over raw
paths for bounded cardinality; App Platform from source over Droplet/Kubernetes/registry until
multiple services or digest deploys are needed; managed database over self-hosted.

---

## 14. Assumptions

| # | Assumption | If wrong |
|---|---|---|
| A1 | Files ≤ 100 MiB, typically far smaller | Raise `MAX_UPLOAD_BYTES`; resumable/multipart upload; object storage sooner |
| A2 | "Local file system" is a hard requirement, not a preference | Object storage adapter becomes v1 |
| A3 | Single instance with persistent volume is acceptable for v1 | Swap storage adapter before scaling out |
| A4 | Owner identity comes from the caller's credential; no registration or IdP | Replace `get_current_user` with token validation |
| A5 | Whoever holds a signed URL is authorised for the TTL | Per-link revocation and/or download caps |
| A6 | Links need no individual revocation in v1; deleting the file suffices | `revoked_links` table checked by `lid` |
| A7 | Server clocks are NTP-synchronised within seconds | Skew tolerance on verification |
| A8 | Clients retry uploads after timeouts | Idempotency-Key already present |
| A9 | Audit events must be durable and written with the action | Already transactional |
| A10 | Uploaded content is trusted enough to store verbatim | Scanning worker and `quarantined` state |
| A11 | Modest read/write ratios; no caching | CDN / presigned direct downloads |
| A12 | PostgreSQL is available as a managed dependency | SQLite would break concurrency guarantees; not acceptable |
| A13 | Range requests and resumable downloads not required | Starlette `FileResponse` range support |
| A14 | Deployment target is DigitalOcean App Platform | Dockerfile is platform-neutral |
| A15 | Owners may see deleted files in listings but not download them | Filter `status=available` in list query |

---

## 15. Known limitations

1. **No per-link revocation.** Valid until `exp` unless the file is deleted or the key is removed. Generation is audited per `link_id`, so a revocation check is a small, backward-compatible change.
2. **Single instance because storage is local.** Two instances would each see only their own files; App Platform disk is ephemeral without a volume. The object-storage adapter is the first production step.
3. **No rate limiting**, per key or per IP.
4. **No content scanning.** Files stored and served verbatim as attachments with `nosniff`.
5. **API-key authentication only.** Static configuration; rotation needs a redeploy; no scopes or quotas.
6. **No HTTP range requests or resumable transfers.**
7. **No per-owner storage quota.** A single key could fill the disk.
8. **Idempotent replays still transfer the bytes** before being discarded.
9. **Audit table grows unbounded.** No partitioning or retention job.
10. **Orphaned bytes need a sweep** after a crash between storage write and DB commit.
11. **Performance validated only at development scale.**
12. **No alerting integration configured**; rules are documented, not provisioned.
13. **Image build verified only in CI** (no local Docker daemon).
14. **Owner listing includes deleted files**; no `status` filter parameter yet.

---

## 16. Scaling path

```
Stage 1 (now)   1 × API (basic-xs) ── Managed PostgreSQL ── local private disk
                Breaks first at 10×: upload CPU (SHA-256 while streaming) and disk write
                throughput on one instance, then disk capacity. Downloads are sendfile and cheap.

Stage 2         N × API ── PostgreSQL ── Spaces/S3 via SpacesFileStorage (same FileStorage port)
                Handlers and tests unchanged; storage unit tests gain a second parametrised impl.
                Optional 302 to presigned GET after the same checks and audit write.
                Bottleneck moves to DB connections (N × pool) and audit write volume.

Stage 3         PgBouncer; read replica for list/audit; partition audit_events by month;
                write-behind batching for download events only (never link.generated).

Stage 4         Same-transaction outbox → durable queue → workers (scan, thumbnails, retention).
                status gains processing/quarantined in domain/files/state.py.

Stage 5         CDN for presigned downloads; edge rate limiting per key and IP; WAF on download
                path; multi-region only if residency or latency requires it.
```

Unchanged throughout: the signing scheme, the error contract, the domain layer and the handlers.

---

## 17. Review talking points

- **Prioritised**: the security-critical path provably correct (signature → expiry → state → bytes, DB untouched on bad signatures, tamper/transplant/extend tests, restart survival); data integrity under concurrency (constraints as arbiters, bytes-before-metadata with cleanup, 16-way race on PostgreSQL); operability (one envelope, request ids everywhere, JSON logs, bounded metrics, truthful readiness, graceful shutdown, fail-fast config); delivery (migrations with drift check, non-root image, App Platform spec with pre-deploy migrations, CI on PostgreSQL and the built container).
- **Intentionally skipped**: per-link revocation, rate limiting, malware scanning, range requests, OIDC, object storage, quotas, audit retention, load testing. Each has an evolution path in section 15.
- **Biggest technical risk**: local-disk storage couples bytes to one instance and, on App Platform, to the container lifecycle. Mitigated by the `FileStorage` port, not in code.
- **Biggest production risk**: ephemeral disk losing files on redeploy; no per-link revocation if a link leaks.
- **Biggest assumption**: that "local file system" is a hard requirement rather than a simplification.
- **Most important decision**: stateless HMAC links (ADR-004). Restart survival and horizontal scaling come free; the download hot path is one HMAC, one PK lookup, one insert; the cost is per-link revocation.
- **Found and fixed along the way**: SQLite tolerated `files` and `audit_events` flushing in either order; PostgreSQL's FK rejected it. An explicit flush fixed it, and it is the concrete argument for integration tests against the real database in CI.
- **With one more day**: `SpacesFileStorage` with parametrised storage tests; `revoked_links` table and `DELETE /v1/files/{id}/links/{link_id}`; per-key rate limiting; `status` filter on listing; per-owner quota; alert rules and dashboard as code.
- **With one more month**: OIDC and a tenant model; outbox → queue → workers with `quarantined`; presigned downloads with CDN and range requests; `audit_events` partitioning and SIEM export; load testing, capacity model, error budget policy.
- **Migrating to async**: add `processing`/`quarantined` to the state machine (downloads already refuse anything not `available`); write an outbox row in the upload transaction; worker scans and sets the final status with an audit event; API contract gains only the `processing` status.

---

## 18. Index of source documents

| File | Lines | What it adds beyond this document |
|---|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | 288 | Full narrative anchor for the technical review; evolution path diagram |
| [API.md](API.md) | 239 | Full request/response examples per endpoint, pagination and timestamp conventions |
| [DECISIONS.md](DECISIONS.md) | 223 | Twelve ADRs with alternatives considered in full |
| [SECURITY.md](SECURITY.md) | 96 | Threat model and control list in full |
| [TESTING.md](TESTING.md) | 152 | Test-by-test description of every unit and API module |
| [DEPLOYMENT.md](DEPLOYMENT.md) | 146 | `doctl` commands, health verification, log access |
| [OPERATIONS.md](OPERATIONS.md) | 111 | Dashboard panel definitions, capacity notes |
| [RUNBOOK.md](RUNBOOK.md) | 153 | Step-by-step incident procedures with PromQL |
| [OBSERVABILITY.md](OBSERVABILITY.md) | 74 | Example log line, trace boundaries, dashboard rows |
| [FAILURE_MODES.md](FAILURE_MODES.md) | 30 | The failure table (reproduced in section 9) |
| [SCALING.md](SCALING.md) | 61 | Stage-by-stage scaling detail |
| [TRADEOFFS.md](TRADEOFFS.md) | 113 | Chosen because / not chosen / when I would change, per trade-off |
| [ASSUMPTIONS.md](ASSUMPTIONS.md) | 22 | Assumption table with basis column |
| [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) | 35 | The numbered list (reproduced in section 15) |
| [REVIEW_NOTES.md](REVIEW_NOTES.md) | 92 | Interview-style answers (condensed in section 17) |
| [diagrams/](diagrams/) | | `architecture.mmd` (Mermaid source) and `architecture.svg` |
