# Operations

## Service overview

Stateless FastAPI process serving owner APIs and public signed downloads. Persistent state:
PostgreSQL (metadata, audit) and a private storage directory (bytes). Single instance in v1.

## Dependencies

| Dependency | Used for | If down |
|---|---|---|
| PostgreSQL | metadata, audit, idempotency arbitration | readiness 503; requests 503 `SERVICE_UNAVAILABLE`; uploads roll back and remove bytes |
| Local disk (`STORAGE_ROOT`) | file bytes | readiness 503 if unwritable; downloads of missing bytes 500 `STORAGE_INCONSISTENT` |
| App Platform edge | TLS, routing, health checks | platform-level |

## Health model

- `GET /health/live`: process alive. Never checks dependencies. Failing it should restart the
  container.
- `GET /health/ready`: startup complete, `SELECT 1` succeeds, storage root writable. Failing it
  should remove the instance from rotation. Goes 503 immediately on SIGTERM.

## Startup and shutdown

```
start: configure logging → validate settings (fail fast) → engine → storage.ensure_ready()
       (mkdir 0700, write probe) → tracing (optional) → ready=true → "service started"
SIGTERM: ready=false → uvicorn stops accepting → in-flight finish (≤ SHUTDOWN_TIMEOUT_SECONDS)
       → engine.dispose() → logging.shutdown() → exit
```

## Metrics

| Metric | Type | Labels | Use |
|---|---|---|---|
| `http_requests_total` | counter | method, route, status_code | traffic, error rate |
| `http_request_duration_seconds` | histogram | method, route | latency SLO |
| `http_requests_in_progress` | gauge | method | saturation |
| `files_uploaded_total`, `upload_bytes_total` | counter | | business volume |
| `upload_failures_total` | counter | reason | why uploads fail (`UploadTooLarge`, `EmptyUpload`, `idempotency_conflict`, `persistence_error`) |
| `links_generated_total`, `downloads_total`, `files_deleted_total` | counter | | business volume |
| `download_failures_total` | counter | reason (`signature`, `expired`, `not_found`, `unavailable`, `storage_missing`) | abuse vs. bugs |
| `db_query_duration_seconds` | histogram | | DB latency |
| `db_pool_connections` | gauge | | pool saturation |

## Logging

One JSON object per line to stdout. Fields: `timestamp`, `level`, `logger`, `service`,
`environment`, `message`, `request_id`, `user_id`, plus per-event extras (`file_id`, `link_id`,
`ttl_seconds`, `status_code`, `duration_ms`, `client_ip`, `route`). Health and metrics requests
are not access-logged unless they fail. Never logged: API keys, signing secrets, signatures,
`DATABASE_URL`, file contents.

## Tracing

Set `OTEL_ENABLED=true`, `OTEL_EXPORTER_OTLP_ENDPOINT`, and install the `otel` extra. FastAPI
and SQLAlchemy are instrumented; health/metrics are excluded.

## Dashboards

1. **Traffic and errors**: `sum by (route, status_code) (rate(http_requests_total[5m]))`;
   5xx ratio excluding 4xx.
2. **Latency**: p50/p95/p99 from `http_request_duration_seconds_bucket` per route; separate
   panel for `/v1/download/{file_id}`.
3. **Business**: uploads, links, downloads per minute; `download_failures_total` by reason.
4. **Dependencies**: `db_query_duration_seconds` p95, `db_pool_connections` vs pool size,
   readiness status.

## Alerts

| Alert | Condition | Severity |
|---|---|---|
| High 5xx | 5xx ratio > 1% for 5 min | page |
| Readiness failing | `/health/ready` non-200 for 2 min | page |
| Storage inconsistency | `download_failures_total{reason="storage_missing"}` > 0 in 10 min | page (data integrity) |
| Latency | p95 `POST /v1/files` > 2 s or `GET /v1/download/{file_id}` > 500 ms for 10 min | ticket |
| Pool saturation | `db_pool_connections` ≥ pool_size for 5 min | ticket |
| Signature failures spike | `download_failures_total{reason="signature"}` rate 10× baseline | ticket (probing) |
| Deployment failed | App Platform alert | page |

## SLOs

- Availability (non-5xx over all requests, excluding client 4xx): 99.9% monthly.
- Download p95 latency (time to first byte): < 250 ms.
- Link generation p95: < 150 ms.
- Upload p95 for ≤ 10 MiB: < 2 s.

## Capacity

Single `basic-xs` instance handles development-scale load comfortably; the API is I/O bound.
Watch `http_requests_in_progress` and disk usage of `STORAGE_ROOT`. Disk is the first hard
limit: `upload_bytes_total` minus deletions approximates usage.

## Scaling

See [SCALING.md](SCALING.md). Horizontal scaling requires shared storage first.

## Common failures

See [FAILURE_MODES.md](FAILURE_MODES.md) and [RUNBOOK.md](RUNBOOK.md).

## Operational procedures

- **Rotate signing key**: add `k2:<secret>` to `SIGNING_KEYS`, set `SIGNING_ACTIVE_KEY_ID=k2`,
  redeploy; remove `k1` after `MAX_LINK_TTL_SECONDS` have elapsed.
- **Rotate an API key**: add the new pair, redeploy, remove the old pair, redeploy.
- **Revoke all links for a file**: `DELETE /v1/files/{id}` as the owner (or set status via SQL
  in an emergency: `UPDATE files SET status='deleted', deleted_at=now() WHERE id=…`).
- **Reconcile orphaned bytes**: list objects under `STORAGE_ROOT/<shard>/` not present in
  `files.storage_key` with `status='available'`, older than one hour, and delete them.
- **Audit export**: `COPY (SELECT … FROM audit_events WHERE created_at >= …) TO STDOUT CSV`.
