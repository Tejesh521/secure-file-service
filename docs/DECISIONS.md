# Architecture Decision Records

Lightweight ADRs. Each records the decision, the reasoning, alternatives considered, the
trade-off accepted, and what would change at larger scale.

---

## ADR-001 — FastAPI for the HTTP layer

**Decision.** Use FastAPI with Pydantic v2 and Uvicorn.

**Why.** Declarative validation of headers, query parameters and bodies with precise 422 details;
OpenAPI generated from the code (the committed snapshot is contract-tested); dependency injection
that keeps handlers free of framework objects; first-class multipart streaming via `UploadFile`.

**Alternatives.** Flask (validation and OpenAPI by hand), Django REST Framework (heavier than the
problem, ORM coupling), Starlette alone (would re-implement validation).

**Trade-offs.** Pydantic coupling in the schema layer; ASGI middleware must be written carefully
so streaming responses are not buffered (done as raw ASGI callables, not `BaseHTTPMiddleware`).

**At scale.** Framework choice matters less than the storage and data decisions below.

---

## ADR-002 — PostgreSQL with SQLAlchemy 2.x and Alembic

**Decision.** PostgreSQL for metadata and audit; SQLAlchemy 2.x typed ORM; Alembic migrations.

**Why.** Constraints are the only safe arbiter for concurrent idempotent uploads
(`UNIQUE(owner_id, idempotency_key)`) and object identity (`UNIQUE(storage_key)`). `JSONB` holds
audit metadata without a schema change per event type. `TIMESTAMPTZ` keeps expiry math honest.
Alembic gives reviewable, reversible schema changes; `alembic check` in CI prevents drift.

**Alternatives.** SQLite (fine for tests, no concurrent writers, no managed offering); a
document store (no constraints to lean on); raw SQL (more code for the same guarantees).

**Trade-offs.** One more managed dependency; ORM indirection. Mitigated by keeping repositories
thin and the domain free of SQLAlchemy.

**At scale.** Managed cluster with read replica for list/audit queries; monthly partitioning of
`audit_events`; connection pooling (PgBouncer) once replicas × pool size approaches the server's
connection limit.

---

## ADR-003 — Local filesystem storage behind a `FileStorage` port

**Decision.** Store bytes on the local filesystem under a private root, addressed by
server-generated keys, via a `FileStorage` Protocol implemented by `LocalFileStorage`.

**Why.** The brief requires the local file system. Isolating it behind a port means the domain
and handlers never see paths, and swapping in object storage is one new adapter.

**Design rules.** Random UUID keys sharded by two hex chars (bounded directory fan-out); write to
`.tmp` then `os.replace` (atomic, never a half-written object under its final key); size enforced
while streaming; root created `0700`; `path_for` refuses keys that resolve outside the root.

**Alternatives.** Object storage from day one (better durability, but not what was asked and
adds a dependency to the exercise); database BLOBs (bloats the OLTP store).

**Trade-offs.** Local disk is per-instance and, on App Platform, ephemeral unless a volume is
attached, so version 1 runs one instance. This is the most important operational limitation and
is documented as such.

**At scale.** `SpacesFileStorage` (S3 API) with the same interface; optionally issue presigned
object-storage GETs from the download endpoint so bytes bypass the API.

---

## ADR-004 — Stateless HMAC-signed links with a key ring

**Decision.** A link is `/v1/download/{file_id}?exp&lid&kid&sig` where
`sig = base64url(HMAC-SHA256(key[kid], "v1\n{file_id}\n{lid}\n{exp}"))`. No `links` table.

**Why.** Validity must survive restarts and be checkable by any replica; a secret in
configuration achieves that with zero shared state. Including `exp` and `file_id` in the message
makes the link unforgeable, non-extendable and non-transplantable. `kid` allows rotating secrets:
new links use the active key, old links verify against any key still in the ring. Constant-time
comparison prevents timing leaks. The signer is pure (no clock, no I/O) so it is trivially
testable; expiry is checked by the service with an injected clock.

**Alternatives.** Random opaque tokens in a `links` table (revocable and countable, but every
download is a DB lookup before any check, and links die if the table is lost); JWT (larger URLs,
more parsing surface, no benefit over HMAC here); asymmetric signatures (unnecessary: only the
service verifies).

**Trade-offs.** Individual links cannot be revoked before expiry; the coarse revocation is
deleting the file (all links → 410) or rotating the key out of the ring (all links signed with
it → 403). Every link generation *is* recorded in `audit_events` with its `link_id`, so
revocation can be added later as a `revoked_links` check keyed by `lid` without changing the URL
format.

**At scale.** Unchanged; HMAC verification is microseconds and needs no coordination.

---

## ADR-005 — Owner identity via static API keys

**Decision.** Owner endpoints require `X-API-Key`; keys map to user ids in configuration
(`API_KEYS=key:user,...`), compared in constant time across all keys.

**Why.** The brief requires that each file be associated with a user id but defines no identity
provider, user store or authorisation model. Static keys satisfy the requirement with the least
invented machinery, and the mapping is a single dependency (`get_current_user`) that can be
replaced with JWT/OIDC validation without touching handlers.

**Alternatives.** Implementing JWT issuance and a users table (invents requirements the exercise
did not state); no authentication (fails the "associated with a specific user ID" requirement and
makes ownership meaningless).

**Trade-offs.** Key rotation is a config change and redeploy; no per-user rate limits or scopes;
keys are bearer secrets and must travel over TLS only (the platform edge terminates TLS and
production refuses a non-HTTPS `PUBLIC_BASE_URL`).

**At scale.** OIDC access tokens from the organisation's IdP; `owner_id` becomes the token's
subject; the rest of the system is unchanged.

---

## ADR-006 — Synchronous processing, no queue

**Decision.** Upload, hash, persist and audit complete inside the request. No broker or worker.

**Why.** All steps are I/O-bound and fast at the target sizes; there is nothing to defer.
Every component not added is one fewer thing to secure, monitor, deploy and explain. Time saved
went into constraints, tests against real PostgreSQL, observability and deployment.

**Alternatives.** Celery/Redis or a managed queue for "upload accepted, processing later".

**Trade-offs.** If a slow step is ever added (malware scan, transcoding), request latency grows
until it is moved out.

**At scale.** Transactional outbox row written in the same commit as `files`; worker fleet
consumes it; `status` gains `processing`/`quarantined`; the state machine in
`domain/files/state.py` is the single place to add those transitions.

---

## ADR-007 — One error envelope, stable machine-readable codes

**Decision.** Every non-2xx response is
`{"error": {"code", "message", "details": [{"field","reason"}], "request_id"}}`. Domain
exceptions declare their `code`; a single table maps exception type → HTTP status.

**Why.** Clients branch on `code`, not on prose; operators paste `request_id` into logs; the
contract test asserts every documented error response uses this schema and that the OpenAPI
snapshot is truthful (FastAPI's default `HTTPValidationError` is replaced).

**Trade-offs.** Slightly more code than raising `HTTPException(detail=...)`. Worth it.

---

## ADR-008 — Idempotent uploads keyed per owner, arbitrated by the database

**Decision.** Optional `Idempotency-Key` header; scope `(owner_id, key)`; replay returns the
existing record with 200 if the content hash matches, 409 otherwise; a unique constraint
resolves concurrent duplicates.

**Why.** Uploads are exactly the kind of request clients retry after a timeout. Hashing the new
payload before deciding costs one streamed write that is then discarded, but makes "same key,
different content" detectable rather than silently returning the wrong file.

**Trade-offs.** A replay still transfers the bytes once. Acceptable at 100 MiB; at larger sizes
a two-step "initiate, then PUT" protocol would avoid it.

---

## ADR-009 — Separate liveness and readiness; readiness gates traffic

**Decision.** `/health/live` returns 200 whenever the process can run a coroutine.
`/health/ready` checks startup completion, `SELECT 1` on the database and that the storage root
is writable, and returns 503 with per-check detail otherwise. On SIGTERM readiness flips to
`unavailable` before in-flight requests drain.

**Why.** Restarting a healthy process because its database blipped makes outages worse; routing
traffic to an instance that cannot write files makes uploads fail. App Platform's health check
points at readiness.

---

## ADR-010 — Observability built in, tracing optional

**Decision.** JSON logs with `request_id`/`user_id` from ContextVars; Prometheus metrics with
template-route labels; OpenTelemetry behind `OTEL_ENABLED` and an optional extra.

**Why.** Logs and metrics are free and always useful. Tracing needs a backend; making it optional
keeps the base image small and guarantees the service never fails to start because a collector is
unreachable.

---

## ADR-011 — Ubuntu 24.04 based image, built by App Platform from the repository

**Decision.** Multi-stage image on `ubuntu:24.04` LTS using the distribution's Python 3.12
(`python3`, `python3-venv`); the virtualenv is copied into a runtime stage that holds only the
interpreter, CA certificates and curl; non-root uid 10001; `HEALTHCHECK`; `uvicorn` without
`--reload`. App Platform builds it from GitHub on push; migrations run as a `PRE_DEPLOY` job.

**Why.** Ubuntu LTS is the organisation's standard base: long security support, a familiar
package set for operators, and the same OS family as the development environment. Using the
distribution interpreter keeps CVE handling in one place (`apt-get upgrade` at build time).
One artifact for CI smoke test, optional Compose and production.

**Alternatives.** `python:3.12-slim` (smaller, but Debian-based and a second OS to track);
distroless (harder to debug, no shell for the HEALTHCHECK).

**Trade-offs.** Slightly larger image than slim. Image builds are not possible in the local
development environment (no Docker daemon), so they are verified in CI; local work uses
`make local` with a virtualenv and a local PostgreSQL.

**At scale.** Push immutable, scanned images to a registry from CI and deploy by digest.

---

## ADR-012 — Soft delete with bytes removed after commit

**Decision.** `DELETE` marks `status=deleted`, writes the audit row, commits, then unlinks the
object. Unlink failures are logged, not raised.

**Why.** Metadata is the source of truth for what may be served. A deleted row with lingering
bytes is unreachable (downloads check status) and reclaimable; the reverse would be a serving
error. Soft delete keeps the audit trail attached to a real row.
