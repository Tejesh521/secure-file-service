# Architecture Review Guide

A 45-minute discussion guide. Speak from this; do not read it.

## 30-second summary

I built a stateless FastAPI service backed by PostgreSQL and a private local storage directory.
Owners authenticate with an API key, upload files that are streamed and hashed into
server-named objects, and mint HMAC-signed download links with a TTL. The download endpoint is
public and fails closed: signature, then expiry, then file state, then bytes. Every upload, link
generation, download and delete is an audit row written in the same transaction as the action.
The service ships one error envelope, request correlation into logs and audit, Prometheus
metrics, split health probes, graceful shutdown, migrations with a drift check, a non-root image,
a Compose stack, an App Platform spec, and CI that runs against real PostgreSQL and smoke-tests
the built container. I kept processing synchronous and storage local because the brief asked for
it and the workload does not justify more; the ports make object storage and a worker fleet an
adapter swap, not a rewrite.

## 2-minute architecture walkthrough

Use `docs/ARCHITECTURE.md` §4 and §5.

1. **Edge**: App Platform terminates TLS and routes on `/health/ready`.
2. **Middleware**: request id (preserve or mint) → body-size limit (declared and streamed) →
   access log + metrics with template routes.
3. **Auth**: `X-API-Key` → `owner_id`, constant time. Download route skips auth.
4. **Use cases**: `UploadFileHandler`, `CreateSignedLinkHandler`, `DownloadService`,
   `DeleteFileHandler`, queries. Each owns one transaction.
5. **Domain**: entities, ports (`FileRepository`, `AuditRepository`, `FileStorage`, `Clock`),
   pure services (filename hygiene, TTL, expiry), state machine.
6. **Infrastructure**: SQLAlchemy repositories, `LocalFileStorage` (random keys, temp + atomic
   rename, streaming cap), engine with timeouts.
7. **Data**: `files`, `audit_events`, two composite indexes, two unique constraints.

Then the two lifecycles: upload (bytes first, then metadata + audit atomically) and download
(HMAC before any I/O).

## Major decisions (ADR numbers in `docs/DECISIONS.md`)

- **ADR-004 stateless HMAC links with key ring.** Why: restart and replica safety with no
  shared state; expiry and file id inside the signed message. Cost: no per-link revocation;
  kept reachable via audited `link_id`.
- **ADR-005 static API keys.** Why: brief needs a user id, defines no IdP. Single dependency to
  swap for OIDC.
- **ADR-003 local disk behind a port.** Why: required. Cost: single instance, ephemeral on App
  Platform without a volume. Adapter swap to Spaces is the first production step.
- **ADR-006 synchronous processing.** Why: nothing slow to defer. Seam: outbox row.
- **ADR-008 idempotency arbitrated by a unique constraint**, hash compared on replay.
- **ADR-007 one error envelope**, contract-tested, OpenAPI made truthful.

## Trade-offs I would defend

- Revocation vs statelessness: chose statelessness, kept revocation cheap to add.
- Bytes-before-metadata: chose it for idempotency correctness and safe failure direction;
  paid with explicit cleanup paths, all tested.
- Soft delete: history and audit integrity over storage of a few rows.
- SQLite in fast tests, PostgreSQL in CI: speed where it is safe, truth where it matters. It
  caught a real FK-ordering bug.

## What I deliberately did not build

Queue/workers, Redis, JWT, object storage, rate limiting, scanning, range requests. Each has a
sentence in `docs/KNOWN_LIMITATIONS.md` and a path in `docs/SCALING.md`.

## How I would scale it

Object storage adapter → N replicas → presigned direct downloads → PgBouncer + read replica →
partitioned audit → outbox/queue/workers. Bottleneck named before technology each time.

## How I would operate it

Probes, JSON logs by request id, metrics with labelled failure reasons, alerts in
`docs/OPERATIONS.md`, runbooks in `docs/RUNBOOK.md`, smoke test as a deploy gate, key rotation
procedure, migrations as pre-deploy job with expand-only policy.

## Likely questions and where the answer lives

| Question | Answer |
|---|---|
| Why does a link survive a restart? | Secret in config, signature over (file, link, exp); `test_link_survives_service_restart` |
| Can someone extend a link? | No: `exp` is in the HMAC message; `test_extending_expiry_in_url_rejected` |
| Can someone use a link for another file? | No: `file_id` is in the message; `test_link_for_one_file_cannot_fetch_another` |
| What happens on concurrent retries? | Unique constraint; 16-way race test on PostgreSQL |
| What if the DB dies mid-upload? | Bytes removed, 503, readiness 503; `test_persistence_failure_removes_orphaned_bytes` |
| Where are audit events written? | Same transaction as the action; `create_signed_link.py` |
| How do you rotate keys? | Key ring + `kid`; runbook entry |
| Why 404 for another owner's file? | Anti-enumeration; `test_other_owner_cannot_see_or_probe` |
| Why is the download endpoint unauthenticated? | The signature is the capability, by requirement |
| What breaks first at 10×? | Upload CPU/disk on one instance; `docs/REVIEW_NOTES.md` |
| Why not S3 from the start? | The brief said local FS; adapter swap is one file |
| Why not JWT? | No identity requirements supplied; ADR-005 |

## Presentation diagram

```
                 ┌──────────────┐        ┌──────────────┐
                 │    Owner     │        │  Link holder │
                 │  X-API-Key   │        │  (anonymous) │
                 └──────┬───────┘        └──────┬───────┘
                        │ HTTPS                  │ HTTPS signed URL
                        ▼                        ▼
            ┌────────────────────────────────────────────┐
            │ DigitalOcean App Platform · TLS · /ready   │
            └──────────────────────┬─────────────────────┘
                                   ▼
            ┌────────────────────────────────────────────┐
            │ FastAPI (stateless)                         │
            │  middleware: request id · body limit ·      │
            │              access log · metrics           │
            │  api → application → domain ← infra         │
            │  upload · sign · download · delete · query  │
            └──────────┬───────────────────────┬─────────┘
                       ▼                       ▼
            ┌────────────────────┐   ┌─────────────────────┐
            │ Managed PostgreSQL │   │ Private storage root│
            │ files · audit      │   │ random keys · 0700  │
            └────────────────────┘   └─────────────────────┘

        ┌──────────────────────────────────────────────────┐
        │ Operational plane: JSON logs · /metrics ·        │
        │ /health/live · /health/ready · X-Request-ID      │
        └──────────────────────────────────────────────────┘

Future:  N × API ── Spaces (adapter) ── outbox → queue → workers ── PostgreSQL + replica
```
