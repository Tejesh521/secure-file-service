# Review Notes

Answers to the questions an architecture review is likely to ask, in one place.

## What I prioritised

1. Getting the security-critical path right and provably so: signature → expiry → state →
   bytes, with the database untouched on bad signatures, and tests for tampering, transplanting
   and extending links, plus restart survival.
2. Data integrity under concurrency: unique constraints as arbiters, bytes-before-metadata with
   cleanup on every failure path, a real 16-way race test on PostgreSQL.
3. Operability: one error envelope, request ids everywhere (including audit rows), JSON logs,
   bounded-cardinality metrics, readiness that tells the truth, graceful shutdown, fail-fast
   production configuration.
4. Delivery: migrations with drift check, non-root image, Compose parity, App Platform spec with
   pre-deploy migrations, CI that runs PostgreSQL and smoke-tests the built container.
5. Documentation that a new engineer can operate from.

## What I intentionally skipped

Per-link revocation, rate limiting, malware scanning, range requests, OIDC, object storage,
quotas, audit retention, load testing. Each is listed in
[KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) with its evolution path.

## Biggest technical risk

Local-disk storage. It satisfies the brief but couples bytes to one instance and, on App
Platform, to the container lifecycle. Mitigated by the `FileStorage` port and a documented
adapter swap; not mitigated in code.

## Biggest production risk

Ephemeral disk losing files on redeploy, and no per-link revocation if a link leaks. The first
is fixed by a volume or object storage; the second by a small revocation table.

## Biggest assumption

That "local file system" is a hard requirement of the exercise rather than a simplification.
If it is not, object storage moves into v1 and single-instance goes away.

## Most important architectural decision

Stateless HMAC links (ADR-004). It makes restart survival and horizontal scaling free and keeps
the download hot path to one HMAC + one PK lookup + one insert, at the cost of per-link
revocation.

## What breaks first at 10× traffic

Upload CPU (SHA-256 while streaming) and disk throughput on the single instance, then disk
capacity. Downloads are `sendfile` and cheap. Fix order: object storage adapter → N replicas →
presigned direct downloads → PgBouncer/read replica for audit and listing.

## What I found and fixed along the way

Running the suite against real PostgreSQL revealed that the `files` row and its `file.uploaded`
audit row could be flushed in either order; SQLite tolerated it, PostgreSQL rejected the FK.
The fix is an explicit flush after adding the file row. This is the concrete argument for
integration tests against the real database in CI.

## With one more day

- `SpacesFileStorage` adapter + parametrised storage tests against a local S3 container.
- `revoked_links` table and `DELETE /v1/files/{id}/links/{link_id}`.
- Per-key rate limiting middleware (token bucket in PostgreSQL or edge rule).
- `status` filter on listing; per-owner storage quota.
- Provision the alert rules and a dashboard as code.

## With one more month

- OIDC authentication; tenant model.
- Outbox → queue → workers: malware scanning with `quarantined`, thumbnails, retention.
- Presigned direct downloads with CDN; range requests.
- `audit_events` partitioning and export; SIEM integration.
- Load testing against staging; capacity model; error budget policy.

## How I would migrate to asynchronous processing

1. Add `processing` and `quarantined` to the state machine; downloads already refuse anything
   not `available`.
2. In `UploadFileHandler`, write an `outbox` row in the same transaction; set status
   `processing` for owners who opt in (or globally once the worker is proven).
3. Worker consumes the outbox (or a queue fed by it), scans, sets `available`/`quarantined`,
   writes an audit event.
4. Nothing changes in the API contract except the possible `processing` status; clients poll
   `GET /v1/files/{id}` or receive a webhook.

## Why not just do it the way FastAPI projects usually do it

Every decision above is *requirement + constraint + alternatives + trade-off*. For example: the
requirement "valid across restarts" plus the constraint "may run on several replicas" plus the
alternatives "table vs HMAC" plus the trade-off "revocation vs statelessness" gives HMAC with a
key ring and an audit row per generation to keep revocation reachable later.
