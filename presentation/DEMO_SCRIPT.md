# Demo Script

Target: 10 minutes, no fumbling. Have two terminals and a browser tab open before starting.

## Setup (before the call)

```bash
cd secure-file-service
make local                     # terminal 1: local PostgreSQL → migrations → API on :8080 (no Docker)
make smoke                     # terminal 2: confirm green
export K='X-API-Key: dev-key-alice'
export STORAGE=./var/storage   # STORAGE_ROOT from .env
```

(With a Docker daemon available, `make dev` starts the same thing from the Ubuntu 24.04 image.)

Optional: `make seed` so listings are not empty.

## 1. README (30 s)

Open `README.md`. Point at the requirement → implementation table and the Mermaid diagram.
"Everything the brief asked for maps to one handler and one endpoint."

## 2. Architecture (60 s)

Open `docs/ARCHITECTURE.md` §5. Walk the upload and download flows. Emphasise: bytes first,
metadata + audit atomically; signature before any I/O on download.

## 3. Swagger (30 s)

Browser: `http://localhost:8080/docs`. Show the seven operations, the error envelope schema,
the `Idempotency-Key` and `X-API-Key` parameters.

## 4. Upload (45 s)

```bash
curl -s -X POST localhost:8080/v1/files -H "$K" -F 'file=@README.md' | jq .
```

Point out: `id`, `sha256`, `status`, `Location` header. Show the bytes are private:

```bash
ls -la $STORAGE                # shard directories only
find $STORAGE -type f | head   # random object names, no client filenames
```

Random shard directories, no filenames.

## 5. Metadata and listing (20 s)

```bash
export FILE=<id>
curl -s localhost:8080/v1/files/$FILE -H "$K" | jq .
curl -s 'localhost:8080/v1/files?limit=5' -H "$K" | jq .page
```

## 6. Signed link (45 s)

```bash
curl -s -X POST localhost:8080/v1/files/$FILE/links -H "$K" \
  -H 'Content-Type: application/json' -d '{"ttl_seconds": 120}' | jq .
export URL=<url>
```

Explain `exp`, `lid`, `kid`, `sig`. "Nothing about this link is stored except the audit row."

## 7. Download, anonymously (30 s)

```bash
curl -sD - -o /tmp/out "$URL" | head -8       # 200, attachment, no-store, nosniff, ETag
diff /tmp/out README.md && echo identical
```

## 8. Tamper and expiry (45 s)

```bash
curl -s "${URL%?}X" | jq .                   # 403 LINK_SIGNATURE_INVALID
# extend exp in the URL by hand → still 403 (exp is signed)
```

Restart the API and use the same link:

```bash
# terminal 1: Ctrl-C, then `make local` again; then:
curl -s -o /dev/null -w '%{http_code}\n' "$URL"   # 200
```

"Restart survival comes from the secret living in configuration, not memory."

## 9. Audit trail (30 s)

```bash
curl -s localhost:8080/v1/files/$FILE/audit -H "$K" | jq '.items[] | {event_type, actor_id, link_id, request_id, client_ip}'
```

Three events, newest first; the download carries the same `lid` as the generation event.

## 10. Idempotency and validation (45 s)

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8080/v1/files -H "$K" -H 'Idempotency-Key: demo-1' -F 'file=@README.md'   # 201
curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8080/v1/files -H "$K" -H 'Idempotency-Key: demo-1' -F 'file=@README.md'   # 200 replay
curl -s -X POST localhost:8080/v1/files -H "$K" -H 'Idempotency-Key: demo-1' -F 'file=@Makefile' | jq .error.code               # IDEMPOTENCY_KEY_REUSED
curl -s -X POST localhost:8080/v1/files/$FILE/links -H "$K" -H 'Content-Type: application/json' -d '{"ttl_seconds": 0}' | jq .error
```

## 11. Delete kills links (20 s)

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X DELETE localhost:8080/v1/files/$FILE -H "$K"   # 204
curl -s "$URL" | jq .error.code                                                          # FILE_UNAVAILABLE (410)
```

## 12. Tests and CI (60 s)

```bash
make test                # ~2 s, coverage summary
make test-integration    # PostgreSQL: constraints, migrations, 16-way race
```

Open `.github/workflows/ci.yml`: quality → unit → integration with a Postgres service →
docker build, non-root check, container smoke test. Mention `security.yml`.

## 13. Health, logs, metrics (45 s)

```bash
curl -s localhost:8080/health/ready | jq .
# terminal 1 shows JSON log lines with request_id (LOG_FORMAT=json in .env)
curl -s localhost:8080/metrics | grep -E '^(http_requests_total|links_generated_total|downloads_total)'
```

Show template route labels.

## 14. Production deployment (60 s, if deployed)

```bash
python scripts/smoke_test.py https://<app>.ondigitalocean.app
```

Open `.do/app.yaml`: PRE_DEPLOY migrate job, readiness health check, SECRET envs, single
instance with the storage caveat.

## Close (20 s)

"What I'd do next: object storage adapter, per-link revocation, rate limiting. Each is one
adapter or one table because of where the boundaries are." Open `docs/REVIEW_NOTES.md`.

## If something fails

- API not up: check terminal 1; `pg_isready -h localhost`; `make migrate`.
- Port in use: `PORT=8081 make run` and adjust URLs.
- Keep going: the test suite and CI screenshots demonstrate the same behaviour.
