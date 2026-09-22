# Trade-offs

Each entry follows: chosen because → not chosen → when I would change.

## Stateless HMAC links vs a `links` table

**Chosen because**: validity survives restarts and works on any replica with no shared state;
verification is microseconds and requires no I/O; the URL is self-describing; rotation is a
config change.

**Not chosen**: opaque random tokens looked up in a table.

**Cost accepted**: no per-link revocation, no per-link download counter or single-use links.
Mitigations: short TTLs, deleting the file kills all links, every generation is audited with
its `link_id`.

**When I would change**: the first real request for "revoke this one link" or "one-time
download". Add a `revoked_links(link_id)` or `links` table consulted after signature check;
URL format stays the same.

## Synchronous processing vs queue and workers

**Chosen because**: every step is fast I/O; no durability gain from deferring; one fewer
component to run, secure and explain; time went to correctness and operability instead.

**Not chosen**: Celery/Redis, managed queue, Kafka.

**When I would change**: any step that is slow or flaky (malware scan, thumbnailing, webhook
notification), or burst absorption needs. Seam: transactional outbox row in the same commit as
`files`.

## Local filesystem vs object storage

**Chosen because**: required by the brief; simplest possible durable write with atomic rename;
`sendfile` downloads.

**Not chosen**: DigitalOcean Spaces / S3.

**Cost accepted**: single instance; ephemeral disk on App Platform without a volume; disk is the
first capacity ceiling.

**When I would change**: before any real users. It is one adapter behind `FileStorage`.

## Static API keys vs JWT/OIDC

**Chosen because**: the brief requires a user id but no identity system; keys deliver ownership
with minimal invented machinery; single dependency to replace.

**Not chosen**: JWT issuance with a users table; no auth.

**When I would change**: as soon as an IdP exists.

## PostgreSQL vs SQLite

**Chosen because**: concurrent writers, real constraints as arbiters, `JSONB`, `TIMESTAMPTZ`,
managed offering. SQLite remains the fast test double for unit/API tests, while everything
PostgreSQL answers differently runs against PostgreSQL in CI.

**When I would change**: never for production; SQLite hid a real insert-ordering bug that
PostgreSQL's FK caught.

## FastAPI vs Flask

**Chosen because**: validation, OpenAPI and DI out of the box; streaming uploads; typed.

**Cost accepted**: Pydantic coupling in schemas; care needed with middleware to avoid buffering.

## ORM vs raw SQL

**Chosen because**: typed models, Alembic autogenerate with drift check, portable across the
test double and PostgreSQL. Repositories are thin; queries are simple.

**When I would change**: hot analytical queries on `audit_events` would move to hand-written
SQL or a reporting store.

## Hash-before-decide idempotency vs key-only idempotency

**Chosen because**: detecting "same key, different content" prevents returning the wrong file
after a client bug; the cost is one discarded write on replay.

**When I would change**: very large files; move to initiate/PUT/complete protocol.

## Soft delete vs hard delete

**Chosen because**: audit trail stays attached to a real row; owners can see history; bytes are
removed immediately so storage is reclaimed.

**When I would change**: legal erasure requirements would add a scheduled purge of deleted rows
(cascade removes audit rows).

## App Platform (from source) vs Droplet/Kubernetes vs registry deploys

**Chosen because**: managed TLS, health checks, deploys on push, managed PostgreSQL; the exercise
does not reward registry or cluster work.

**When I would change**: multiple services, custom networking, or the need to deploy by image
digest from CI.

## Managed database vs self-hosted

**Chosen because**: backups, failover and patching are somebody else's job; the exercise is about
the service.

## Template route labels vs raw paths in metrics

**Chosen because**: bounded cardinality; Prometheus stays healthy under id-heavy traffic. Cost:
a little code to rebuild the template from path params in nested routers.

## Bytes-first vs metadata-first on upload

**Chosen because**: the content hash is needed for idempotency decisions; orphaned bytes are
harmless and sweepable, metadata without bytes would be a serving error. Cost: an explicit
cleanup on every persistence failure path (tested).
