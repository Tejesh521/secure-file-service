# Runbook

Each entry: symptoms → checks → mitigation → verification. Correlate everything with
`request_id` from error bodies or response headers.

---

## Elevated 5xx

**Symptoms**: 5xx ratio alert; users report `INTERNAL_ERROR`.

**Check**
1. Which routes? `sum by (route) (rate(http_requests_total{status_code=~"5.."}[5m]))`.
2. Which codes? `STORAGE_INCONSISTENT` → see *Bytes missing*. `SERVICE_UNAVAILABLE` → see
   *Database unavailable*. `INTERNAL_ERROR` → continue.
3. Logs: filter `level=ERROR`, pick a `request_id`, read the stack trace.
4. Did a deployment land in the last 30 minutes?

**Mitigate**: roll back the deployment if correlated; otherwise fix forward.

**Verify**: 5xx ratio returns to baseline; smoke test passes.

---

## High latency

**Symptoms**: p95 alert on a route.

**Check**
1. `db_query_duration_seconds` p95: database or app?
2. `db_pool_connections` near pool size → pool exhaustion.
3. `http_requests_in_progress` high → saturation; CPU on the instance (uploads hash bytes).
4. Large uploads in progress? `upload_bytes_total` rate.

**Mitigate**: scale instance size (CPU) for upload-heavy load; raise pool size within DB limits;
find the slow query in PostgreSQL `pg_stat_statements`.

**Verify**: p95 within SLO for 15 minutes.

---

## Database unavailable

**Symptoms**: `/health/ready` 503 with `database: unavailable`; 503 `SERVICE_UNAVAILABLE`
responses; `database unavailable` ERROR logs.

**Check**
1. Managed database status in the DigitalOcean console.
2. `DATABASE_URL` and `sslmode`; trusted sources / firewall changes.
3. Connection count at the database vs. limit.
4. Recent deployment changing pool settings.

**Mitigate**: platform already stops routing to unready instances. Restore connectivity; if a
config change caused it, roll back. Clients retrying uploads with the same `Idempotency-Key`
are safe.

**Verify**: readiness 200; error rate normal; `SELECT count(*) FROM files` grows again.

---

## Storage unavailable

**Symptoms**: readiness 503 `storage: unavailable`; uploads 500; startup failure
`StorageNotWritable`.

**Check**: volume mounted at `STORAGE_ROOT`; owned/writable by uid 10001; disk space.

**Mitigate**: fix mount/permissions; free space; restart.

**Verify**: readiness 200; upload via smoke test succeeds.

---

## Bytes missing (`STORAGE_INCONSISTENT`)

**Symptoms**: `download_failures_total{reason="storage_missing"}` > 0; ERROR log
`stored object missing` with `file_id`, `storage_key`.

**Check**
1. Was the instance redeployed/restarted without a persistent volume? (App Platform ephemeral
   disk.) If yes, all files uploaded since the last volume snapshot are gone.
2. Volume health; accidental deletion under `STORAGE_ROOT`.

**Mitigate**: restore from volume snapshot if available. Otherwise mark affected rows deleted so
owners see an honest status:
`UPDATE files SET status='deleted', deleted_at=now(), updated_at=now() WHERE id IN (...)`.
Communicate to owners via the audit trail (`file.deleted` events can be inserted with metadata
`{"reason": "storage_loss"}`).

**Verify**: counter stops increasing.

---

## Deployment failure

**Symptoms**: App Platform deployment failed alert.

**Check**
1. PRE_DEPLOY job logs (`migrate`): migration error or DB unreachable.
2. Service startup logs: `ValueError` from settings means missing/invalid secrets or non-HTTPS
   `PUBLIC_BASE_URL`.
3. Health check failing: `/health/ready` output.

**Mitigate**: previous version keeps serving. Fix the cause and redeploy.

**Verify**: deployment succeeds; smoke test passes against the app URL.

---

## Bad migration

**Symptoms**: migrate job fails; or succeeds but the app errors on a column.

**Check**: `alembic history`, `alembic current` against the database; CI `alembic check` result
for the commit.

**Mitigate**: migrations are expand-only by policy, so the previous app version keeps working.
Write a corrective forward migration; avoid `downgrade` in production unless data loss is
acceptable and understood.

**Verify**: `alembic check` clean; app healthy.

---

## Signature failure spike

**Symptoms**: `download_failures_total{reason="signature"}` rate jumps.

**Check**
1. Did a deployment change `SIGNING_KEYS` (key removed)? Then legitimate old links fail:
   re-add the key.
2. Single client IP hammering with garbage → probing; consider edge rate limiting/blocking.

**Verify**: rate returns to baseline.

---

## Rotate the signing key (planned)

1. Generate: `make secret`.
2. Set `SIGNING_KEYS=k2:<new>,k1:<old>` and `SIGNING_ACTIVE_KEY_ID=k2`; redeploy.
3. After `MAX_LINK_TTL_SECONDS` (7 days), remove `k1`; redeploy.

## Rotate the signing key (compromise)

1. Same as above but remove `k1` immediately, accepting that every outstanding link is dead.
2. Watch `reason="signature"` failures for affected users.

## Revoke access to a file

`DELETE /v1/files/{id}` as the owner. Emergency, without the owner's key:
`UPDATE files SET status='deleted', deleted_at=now(), updated_at=now() WHERE id='…'`, then
remove the object at `STORAGE_ROOT/<storage_key>`.
