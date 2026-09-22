# Failure Modes

| Failure | Behaviour | Detection | Recovery |
|---|---|---|---|
| Invalid multipart / missing `file` part | 422 `VALIDATION_ERROR` with field detail | `http_requests_total{status_code="422"}` | client fixes request |
| Empty file | 422 `EMPTY_UPLOAD`; nothing persisted | `upload_failures_total{reason="EmptyUpload"}` | client |
| Oversized upload, declared | 413 before reading body | 413 counter | client |
| Oversized upload, undeclared (chunked) | cut off at limit while streaming; temp file removed; 413 | `upload_failures_total{reason="UploadTooLarge"}` | client |
| Duplicate upload, same key + same content | 200 with existing record; new bytes discarded | `idempotent upload replayed` log | none |
| Duplicate upload, same key + different content | 409 `IDEMPOTENCY_KEY_REUSED` | `upload_failures_total{reason="idempotency_conflict"}` | client chooses a new key |
| Concurrent duplicates racing | unique constraint wins; loser rolls back, re-reads, replays | integration test; `rollbacks` in logs | none |
| Database down during upload | bytes written then removed; 503 `SERVICE_UNAVAILABLE`; readiness 503 | `database unavailable` ERROR log; readiness | reconnect; client retries with same key |
| Database down during link generation | 503; no link issued, no audit row (atomic) | same | client retries |
| Database down during download | 503 (lookup) or, after checks passed, audit insert fails → 503 and no bytes served | same | client retries |
| Storage root unwritable | startup fails fast (`StorageNotWritable`) or readiness 503 `storage: unavailable` | readiness | fix permissions/volume |
| Bytes missing for an available row | 500 `STORAGE_INCONSISTENT`, ERROR log with `file_id` and `storage_key` | `download_failures_total{reason="storage_missing"}` → page | investigate disk/volume; mark file deleted or restore from backup |
| Orphaned bytes after crash between write and commit | unreachable object on disk | disk usage vs `size_bytes` sum | reconciliation sweep |
| Tampered signature / wrong file id / wrong kid | 403 `LINK_SIGNATURE_INVALID`; no DB hit | `download_failures_total{reason="signature"}` | none; alert on spike |
| Expired link | 410 `LINK_EXPIRED` | `reason="expired"` | owner mints a new link |
| Link for deleted file | 410 `FILE_UNAVAILABLE` | `reason="unavailable"` | none (intended) |
| Malformed download params | 422 | 422 counter | client |
| Signing key removed from ring | all links signed with it → 403 | signature failures spike after deploy | re-add key if unintended |
| Clock skew between instances | links appear expired/valid slightly early/late | none (NTP assumed) | NTP; add tolerance if needed |
| Unexpected exception | 500 `INTERNAL_ERROR` with request id only; stack trace logged | 5xx ratio; `unhandled exception` log | investigate by request id |
| Slow query | bounded by `statement_timeout` (5 s) → `OperationalError` → 503 | `db_query_duration_seconds` | index/plan review |
| Pool exhaustion | requests wait up to `pool_timeout`, then 503 | `db_pool_connections` at pool size | raise pool or add replicas/PgBouncer |
| SIGTERM during requests | readiness 503 immediately; in-flight complete within `SHUTDOWN_TIMEOUT_SECONDS`; pool disposed | `shutdown started` log | none |
| Bad configuration in production | process refuses to start with a clear `ValueError` | failed deploy | fix env, redeploy |
| Bad migration | PRE_DEPLOY job fails; old version keeps serving | deployment alert | fix migration; rollback not needed |
| Disk full | writes fail with `OSError` → temp removed → 500; readiness may still pass until probe write fails | disk metrics; 5xx | free space; quotas |
