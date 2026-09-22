# Deployment

## Prerequisites

- A GitHub repository containing this code (App Platform builds from it).
- `doctl` authenticated against the target DigitalOcean account.
- Two secrets ready: `SIGNING_KEYS` and `API_KEYS` (see below).

## Local run without Docker

The development environment has no Docker daemon, so the everyday loop is virtualenv plus a
local PostgreSQL:

```bash
make install && cp .env.example .env
createdb files
make local                    # wait_for_db → alembic upgrade head → uvicorn --reload on :8080
make smoke                    # scripts/smoke_test.py http://localhost:8080
```

## Image build (CI or any host with Docker)

```bash
make build                    # docker build -t secure-file-service:local .
docker run --rm secure-file-service:local id -u     # 10001 (non-root)
make dev                      # compose: postgres → migrate job → api on :8080
```

The image is multi-stage on `ubuntu:24.04` LTS with the distribution's Python 3.12. The
runtime stage has only `python3`, CA certificates and curl, applies security updates at build
time, copies the virtualenv plus `app/`, `migrations/`, `alembic.ini` and two scripts, runs as
uid 10001, exposes 8080 and declares a `HEALTHCHECK` on `/health/live`. The default command
runs Uvicorn with `--proxy-headers`, a graceful-shutdown timeout and no `--reload`. The `docker`
CI job builds it, asserts the non-root uid and smoke-tests the running container.

## DigitalOcean architecture

```
GitHub (main)
   │ push
   ▼
GitHub Actions ─ format · lint · mypy · openapi-check · unit/api/contract · PostgreSQL
                 integration · migration check · docker build · non-root check · smoke
   │
   ▼
DigitalOcean App Platform (.do/app.yaml)
   ├── job  "migrate"  PRE_DEPLOY  → python scripts/wait_for_db.py && alembic upgrade head
   ├── service "api"   Dockerfile  → uvicorn, health check /health/ready, 1 instance
   └── database "files-db"  Managed PostgreSQL 16
```

## Environment variables

Set in `.do/app.yaml` (plain) or the App Platform console (SECRET):

| Variable | Value |
|---|---|
| `ENVIRONMENT` | `production` (turns on config safety rails) |
| `PUBLIC_BASE_URL` | `${APP_URL}` (must be `https://`) |
| `TRUSTED_HOSTS` | `${APP_DOMAIN}` |
| `DATABASE_URL` | `${files-db.DATABASE_URL}?sslmode=require` |
| `STORAGE_ROOT` | `/var/lib/secure-file-service/storage` |
| `SIGNING_KEYS` | SECRET, `k1:<48+ chars>`; generate with `make secret` |
| `API_KEYS` | SECRET, `<key>:<user>,<key>:<user>` |
| `MAX_UPLOAD_BYTES`, `SHUTDOWN_TIMEOUT_SECONDS`, `LOG_LEVEL` | as needed |

The service refuses to start in production with the development defaults, a signing secret
shorter than 32 bytes, or a non-HTTPS base URL, so a mis-set secret shows up as a failed deploy,
not as a live service with a guessable key.

## Database setup

The managed database is declared in the spec; App Platform injects its connection string.
Nothing else is needed: the `migrate` job creates the schema.

## Migrations

`alembic upgrade head` runs as a `PRE_DEPLOY` job on every deployment, before the new service
version receives traffic. Migrations are written to be backward compatible with the previous
application version (expand → migrate → contract) so a rollback of the service never needs a
schema rollback. `alembic check` in CI guarantees models and migrations agree.

## App Platform deployment

```bash
# first time
sed -i 's#YOUR_GITHUB_ORG/secure-file-service#<org>/<repo>#' .do/app.yaml
doctl apps create --spec .do/app.yaml
# set secrets in the console (Settings → api → Environment Variables) or:
doctl apps update <app-id> --spec .do/app.yaml   # after editing SECRET values locally, never commit them

# subsequent deploys: push to main (deploy_on_push: true)
doctl apps list-deployments <app-id>
```

## Health verification

```bash
APP=https://<app>.ondigitalocean.app
curl -s $APP/health/live
curl -s $APP/health/ready      # {"status":"ok","checks":{"startup":"ok","database":"ok","storage":"ok"}}
```

## Smoke test

```bash
SMOKE_API_KEY=<a key from API_KEYS> python scripts/smoke_test.py $APP
```

Exits 0 only if the entire lifecycle (health, auth, upload, metadata, link, download, tamper
rejection, validation, audit, delete, dead link) passes. Wire it as a post-deploy step.

## Logs

App Platform → Runtime Logs shows the JSON lines. Filter by `request_id` (from any error body or
response header) to see everything a request did. `doctl apps logs <app-id> --type run --follow`.

## Rollback

- Service: App Platform → Deployments → Rollback to the previous successful deployment. The
  schema is backward compatible by policy so no database action is needed.
- Configuration: revert the env var and redeploy.
- Signing key compromise: prepend a new key to `SIGNING_KEYS`, set it active, keep the old key
  in the ring only as long as you are willing to honour links signed with it, then remove it
  (all links signed with it → 403).

## Storage caveat

The container filesystem on App Platform is ephemeral: a redeploy or restart loses files stored
under `STORAGE_ROOT` unless a persistent volume is attached. This is why `instance_count` is 1
and why the highest-value next step is the object-storage adapter described in
[SCALING.md](SCALING.md). For a demo it is acceptable; for real users it is not, and
[KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) says so.

## Troubleshooting

| Symptom | Likely cause | Check |
|---|---|---|
| Deploy fails at startup with `ValueError: SIGNING_KEYS must be set…` | Secrets not set | Console env vars; `ENVIRONMENT=production` is doing its job |
| `doctl apps create` → `400 GitHub user not authenticated` | DigitalOcean account has not authorized GitHub | Authorize under Apps → Create App → GitHub, or replace the `github:` blocks with `git: {repo_clone_url: https://github.com/<org>/<repo>.git, branch: main}` for a public repo (no auto-deploy on push; use `doctl apps create-deployment <app-id>`) |
| Migrate job dies with `No module named 'psycopg2'` | Platform injected a bare `postgresql://` URL | Fixed by `normalize_database_url` in `app/core/config.py`, which pins the psycopg 3 driver |
| `/health/ready` 503 `database: unavailable` | DB not reachable / wrong `sslmode` | `DATABASE_URL`, database firewall/trusted sources |
| `/health/ready` 503 `storage: unavailable` | `STORAGE_ROOT` not writable by uid 10001 | Path and volume permissions |
| Links point at `http://localhost:8080` | `PUBLIC_BASE_URL` not set | env var |
| 400 `Invalid host header` | `TRUSTED_HOSTS` does not include the domain | env var |
| Uploads 413 unexpectedly | platform request size limit or `MAX_UPLOAD_BYTES` | both |
