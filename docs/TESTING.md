# Testing

## Philosophy

Tests exist to make three claims with confidence: access control is correct, metadata and bytes
never disagree in a way that serves the wrong thing, and the service behaves predictably when
dependencies fail. Fast tests run without services; the parts that only PostgreSQL can prove
(constraints, ordering, migrations, concurrency) run against real PostgreSQL in CI. Nothing is
mocked that can be faked with an in-memory port implementation, and nothing is faked that the
real dependency would answer differently.

## Pyramid

```
              e2e (3)            deployed instance, opt-in via E2E_BASE_URL
          contract (4)           OpenAPI snapshot == code; real responses validate against schemas
       integration (13)          PostgreSQL: constraints, cascade, JSONB, migrations, 16-way race
           api (52)              full ASGI stack, in-memory SQLite, temp storage dir
          unit (97)              signer, domain rules, handlers with fakes, storage adapter, config
```

166 tests run locally in about two seconds; 98% line coverage with `fail_under = 85`.

## Unit (`tests/unit`)

- `test_security.py`: sign/verify round trip, every field change breaks the signature, tampered
  and garbage signatures, unknown key id, rotation keeps old links valid, a fresh signer with
  the same config verifies old links (restart), constant-time API key lookup.
- `test_domain_services.py`: filename sanitisation (traversal, backslashes, control chars,
  dot names, Unicode, truncation preserving extension), content-type shape, header safety of
  `Content-Disposition`, TTL bounds, expiry boundary (`now == exp` is expired).
- `test_state.py`: `available → deleted` only; deleted is terminal.
- `test_config.py`: pair parsing, derived lists, active key must be in ring, TTL ordering,
  production rails (dev secrets, short keys, non-HTTPS base URL all refused).
- `test_upload_handler.py`: metadata + bytes + audit; idempotent replay discards redundant
  bytes; conflict on different content; keys scoped per owner; simulated race (lookup misses,
  insert collides) falls back to replay; persistence failure removes orphaned bytes; empty and
  oversized rejected before persistence.
- `test_create_link_handler.py`: URL shape and verifiability, audit event contents, default
  TTL, out-of-range TTL, ownership, deleted file, uniqueness of links.
- `test_download_service.py`: valid link audits; **bad signature never touches the database**;
  unknown key id; previous key still verifies; expiry; extending `exp` fails the signature;
  missing/deleted file; missing bytes → `StorageInconsistent`.
- `test_delete_handler.py`: soft delete + unlink + audit; idempotent; ownership; unlink failure
  after commit is logged, not raised.
- `test_local_storage.py`: `0700` directories, hash round trip, sharded random keys, no partial
  files after failure, size cap enforced while streaming (endless reader), exact limit allowed,
  traversal keys refused, idempotent delete, unwritable root detected.
- `test_logging_and_middleware.py`: JSON formatter fields and exception rendering, request-id
  validation, route template reconstruction.

## API (`tests/api`)

Full application via `TestClient`, SQLite in memory (StaticPool), temporary storage root, test
signing keys `k1` (active) and `k0` (rotated).

- Health: liveness has no dependencies; readiness lists checks; 503 when the DB engine fails;
  503 during shutdown with `startup: shutting_down`.
- Metrics: route labels are templates; no per-id cardinality; unmatched paths collapse.
- Auth and errors: 401 with `WWW-Authenticate`; unknown route/method use the envelope; 422
  lists `query.limit`/`query.offset`; malformed JSON; request id generated, preserved when valid,
  replaced when invalid; unexpected exception → sanitised 500 with matching header and body id;
  `OperationalError` → 503; oversized body rejected by `Content-Length` and, separately, when
  chunked with no length.
- Upload: 201 with `Location`, hash, bytes under private root; sanitised filename/content type;
  missing part, empty file, oversize (both paths), auth, idempotent replay (200), conflict
  (409), per-owner key scope, key format.
- Files: metadata; other owner gets identical 404 for missing vs foreign across all routes;
  list is scoped, paginated, newest first, `limit=101` rejected; delete → 410 on link creation
  and is idempotent; audit trail order, contents, pagination.
- Links and download: URL shape; TTL default/bounds/extra fields; download bytes + headers
  (attachment with UTF-8 filename, no-store, nosniff, ETag); reusable until expiry; tampered
  signature; extended `exp`; link for file A cannot fetch file B; expired; unknown `kid`;
  rotated key; deleted file → 410; signed link for missing file → 404; malformed params → 422;
  missing bytes → 500 without leaking; **link minted by one app instance is honoured by a new
  instance after shutdown** (restart survival).

## Integration (`tests/integration`, marker `integration`)

Skipped unless `TEST_DATABASE_URL` points at PostgreSQL. Schema is built by running the real
Alembic migrations from an empty `public` schema and torn down with `downgrade base`.

- Repositories: timezone-aware round trip; `UNIQUE(owner_id, idempotency_key)` raises
  `IntegrityError` naming the constraint; NULL keys do not collide; `storage_key` unique;
  newest-first with stable tiebreak and totals; `set_status` sets `deleted_at`; audit ordering,
  `JSONB` column type, FK cascade; expected indexes exist.
- Migrations: `compare_metadata` reports no drift; downgrade/upgrade round trip.
- API on PostgreSQL: end-to-end lifecycle; **16 concurrent uploads with one `Idempotency-Key`
  produce exactly one row and one `file.uploaded` event**; `statement_timeout` is applied.

This suite caught a real defect during development: with SQLite the file row and its audit row
could be flushed in either order, but PostgreSQL enforced the foreign key and rejected the
audit insert. The fix (flush the file row before dependent inserts) is covered here.

## Contract (`tests/contract`)

- The committed `openapi/openapi.yaml` equals `app.openapi()`; a stale snapshot fails CI with
  the instruction to run `make openapi`.
- Every non-health operation declares error responses, and all of them use `ErrorResponse`.
- Live responses for every 2xx endpoint, health, and a set of 4xx cases validate against the
  schemas the spec declares (JSON Schema 2020-12).
- Documented parameters include `X-API-Key`, `Idempotency-Key` and exactly the four signature
  query parameters.

## End-to-end (`tests/e2e`, marker `e2e`)

Runs against `E2E_BASE_URL` (a Compose stack or the deployed app) with `E2E_API_KEY`. Health,
full lifecycle including anonymous download and tamper rejection, validation and auth.
`scripts/smoke_test.py` covers the same ground as a single command for deploy gates.

## Performance (`tests/performance`, marker `performance`)

Opt-in with `RUN_PERFORMANCE=1`. Asserts generous p95 budgets on the in-process stack (upload of
64 KiB < 250 ms, sign < 100 ms, download < 100 ms, listing flat with 300 files). A regression
guard, not a benchmark.

## Failure cases covered

Database unavailable (503, readiness), duplicate request (replay/409/race), malformed payload
(422 with fields), oversize by declaration and by streaming (413), storage unwritable
(readiness), bytes missing (500 + ERROR log), unexpected exception (sanitised 500 with request
id), shutdown in progress (readiness 503), storage unlink failure after delete (logged).

## Coverage

`pytest --cov` with branch coverage; `fail_under = 85` in `pyproject.toml`; currently 98%.
`app/core/telemetry.py` tracing setup is omitted because it needs the optional extra and a
collector.

## How to run

```bash
make test                # unit + api + contract with coverage
make test-integration    # TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/files_test
make test-e2e E2E_BASE_URL=http://localhost:8080
make test-performance
make verify              # everything CI runs
```

## What was not tested (deliberately)

- Real TLS termination and proxy header handling (platform concern; `--proxy-headers` is set).
- Disk-full behaviour beyond the size cap (would need fault injection at the OS level).
- Multi-instance behaviour on shared storage (version 1 is single-instance by design).
- OpenTelemetry export (optional extra; smoke-tested manually only).
- Load beyond development scale.

## Future testing

Property-based tests for `sanitize_filename` and the signer (Hypothesis); fault injection for
disk-full and partial-write; k6 load profile against a staging deployment; mutation testing on
the download decision chain.
