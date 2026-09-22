# Scaling

Bottlenecks first, then technology.

## Stage 1 — now

```
1 × API (basic-xs) ── Managed PostgreSQL ── local private disk
```

Limits: disk capacity of the instance; single instance CPU for hashing on upload; one process
for downloads (`sendfile`, cheap). First thing to break at 10× traffic: concurrent large uploads
saturating CPU and disk write throughput on one instance, and disk filling.

## Stage 2 — shared object storage, N stateless replicas

```
N × API ── Managed PostgreSQL (primary)
   └──── Spaces / S3 (SpacesFileStorage adapter, same FileStorage port)
```

- Implement `FileStorage` for S3: streamed multipart upload, same random keys, `exists`/`delete`
  via HEAD/DELETE, `open` via GET. Handlers and tests do not change; the storage unit tests get
  a second parametrised implementation against a local S3-compatible container.
- Set `instance_count > 1`. Readiness stays per instance; HMAC links already verify anywhere.
- Optional: download endpoint returns a `302` to a presigned object-storage URL after the same
  checks and audit write. Bytes stop flowing through the API; latency and cost drop.

Bottleneck moves to: database connections (N × pool size) and audit write volume.

## Stage 3 — database headroom

- PgBouncer (transaction pooling) in front of PostgreSQL once connections approach the limit.
- Read replica for `GET /v1/files` and `/audit` queries (repositories gain a read session).
- Partition `audit_events` by month on `created_at`; retention job drops old partitions.
- Batch audit writes for downloads if they dominate (write-behind buffer with at-least-once
  flush; accept slight lag for download events only, never for `link.generated`).

## Stage 4 — asynchronous post-processing

```
API ── PostgreSQL ── outbox ──► durable queue ──► worker fleet ──► object storage / PostgreSQL
```

- Same-transaction outbox row on upload; workers scan for malware, generate thumbnails, apply
  retention, send notifications.
- `status` gains `processing` and `quarantined`; the state machine in `domain/files/state.py`
  is the one place to add transitions; downloads already refuse anything not `available`.

## Stage 5 — edge

- CDN in front of presigned downloads for hot public assets (respecting `no-store` for private
  ones).
- Edge rate limiting per API key and per IP; WAF rules for the download path.
- Multi-region only if data residency or latency requires it; object storage replication plus
  PostgreSQL cross-region replica, with the key ring shared via the secret manager.

## What does not need to change

The signing scheme, the error contract, the domain layer and the handlers. Everything above is
an adapter swap or an added component, which is the point of the port boundaries.
