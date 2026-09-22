# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-09-22

### Added
- Private file upload (`POST /v1/files`) streamed to a non-public storage root with
  server-generated object keys, SHA-256 hashing, size limits and optional
  `Idempotency-Key` replay protection.
- Owner metadata endpoints: get, list (paginated), soft delete and per-file audit trail.
- Signed download links (`POST /v1/files/{id}/links`) using HMAC-SHA256 with a key
  ring for rotation; links survive restarts and are verifiable by any replica.
- Public download endpoint (`GET /v1/download/{id}`) that validates signature, expiry,
  file state and storage presence before streaming bytes.
- Audit events for upload, link generation, download and delete, stored in PostgreSQL.
- Single error envelope, request correlation (`X-Request-ID`), structured JSON logs,
  Prometheus metrics, optional OpenTelemetry tracing.
- Liveness and readiness probes, graceful shutdown, bounded database timeouts.
- Alembic migrations, Ubuntu 24.04 LTS based Docker image (non-root, multi-stage), Docker Compose stack,
  DigitalOcean App Platform spec, GitHub Actions CI and security workflows.
- Docker-free local development loop (`make local`) for environments without a Docker daemon.
- Unit, API, PostgreSQL integration, contract, end-to-end and performance test suites.
