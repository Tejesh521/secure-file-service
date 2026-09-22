# Observability

## Logs

Structured JSON, one object per line, to stdout.

```json
{"timestamp": "2026-09-22T21:11:23.614+00:00", "level": "INFO", "logger": "app.access",
 "service": "secure-file-service", "environment": "production", "message": "request completed",
 "request_id": "req_e33320d2a6c14cc588e05325097e0930", "user_id": "alice",
 "method": "POST", "route": "/v1/files", "path": "/v1/files", "status_code": 201,
 "duration_ms": 37.2, "client_ip": "203.0.113.7", "user_agent": "curl/8.5"}
```

Domain events add their own lines: `file uploaded` (`file_id`, `size_bytes`, `owner_id`),
`signed link generated` (`file_id`, `link_id`, `ttl_seconds`, `key_id`), `download served`
(`file_id`, `link_id`), `file deleted`, `stored object missing` (ERROR). Health and metrics
requests are logged only when they fail.

`LOG_FORMAT=console` gives a human-readable variant for local development.

## Metrics

Prometheus text at `/metrics`. Names are stable and documented in
[OPERATIONS.md](OPERATIONS.md#metrics). Route labels use templates
(`/v1/download/{file_id}`); unmatched paths collapse to `unmatched`, so cardinality is bounded
by the number of routes × methods × status codes.

## Traces

`OTEL_ENABLED=true` plus the `otel` extra (`pip install ".[otel]"`) instruments FastAPI
(server spans per request, excluding health/metrics) and SQLAlchemy (one span per statement),
exported over OTLP/HTTP to `OTEL_EXPORTER_OTLP_ENDPOINT`. Boundaries: HTTP request → dependency
resolution → handler → repository → PostgreSQL. If a queue is added later, propagate context
into the message so worker spans join the trace.

## Request correlation

`RequestIdMiddleware` accepts a client `X-Request-ID` matching `^[A-Za-z0-9_.:-]{8,128}$` or
mints `req_<uuid hex>`. The id is put in the ASGI scope, a `ContextVar` (so every log line
during the request carries it), the response header, every error body and every audit event
row. A support engineer with an error body can find the request's logs and its audit footprint.

## Health checks

| Probe | Checks | Failure action |
|---|---|---|
| `/health/live` | event loop responds | restart container |
| `/health/ready` | startup complete; `SELECT 1`; storage writable | remove from rotation |

## Suggested dashboard

Row 1: requests/s by route; 5xx ratio; p95 latency by route.
Row 2: uploads/min, links/min, downloads/min; `download_failures_total` by reason (stacked).
Row 3: `db_query_duration_seconds` p95; `db_pool_connections`; readiness status; instance count.
Row 4: `upload_bytes_total` rate and cumulative storage estimate; `upload_failures_total` by
reason.

## Suggested alerts

See [OPERATIONS.md](OPERATIONS.md#alerts). The one to insist on: any
`download_failures_total{reason="storage_missing"}` increment pages, because it means metadata
and bytes disagree.

## SLOs

| SLI | Target |
|---|---|
| Availability: non-5xx / all requests (4xx excluded) | 99.9% / 30 days |
| Download p95 latency to first byte | < 250 ms |
| Link generation p95 | < 150 ms |
| Upload p95 (≤ 10 MiB) | < 2 s |
| Validation errors (4xx) | excluded from availability; tracked as a client-quality signal |
| Unexpected 5xx (`INTERNAL_ERROR`, `STORAGE_INCONSISTENT`) | < 0.1% |
