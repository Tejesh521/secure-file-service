# Security

## Threat model

Assets: file bytes (confidentiality), metadata and audit trail (integrity), service availability.

Actors: anonymous internet users holding or guessing URLs; authenticated owners acting on other
owners' files; a compromised client with a leaked API key; an attacker with read access to
configuration.

Out of scope for v1: malware in uploaded content (files are stored and returned verbatim, served
as attachments with `nosniff`), DDoS (edge concern), insider with database write access.

## Controls

### File storage

- Object keys are server-generated UUIDs; client-supplied names influence only display
  metadata. Path traversal is impossible by construction; `path_for` additionally refuses any
  key resolving outside the root.
- Root created `0700`; nothing in the application serves static files; the storage root is not
  under any web-exposed path.
- Writes go to a temp file and are moved atomically; a partially written object is never
  observable under its final key.
- Size enforced by `Content-Length` and again while streaming; a client that lies is cut off.

### Signed links

- `HMAC-SHA256` over `version\nfile_id\nlink_id\nexp` with a server secret; `base64url`
  output; `hmac.compare_digest`.
- Expiry is part of the signed message: extending it changes the signature. File id is part of
  it: a signature for one file cannot fetch another.
- Key ring with `kid`: rotation without breaking outstanding links; removing a key revokes every
  link signed with it.
- Downloads check state after signature: deleted files return 410 even with a valid link.
- Production requires secrets of at least 32 bytes and refuses the development key.

### Authentication and authorisation

- Owner endpoints require `X-API-Key`; comparison is constant time across all configured keys so
  timing does not reveal whether a key exists.
- Every owner query is scoped by `owner_id` in SQL; missing and foreign files return an identical
  404 so ids cannot be enumerated or confirmed.
- The download endpoint is intentionally credential-free; the signature is the capability.

### Input validation

- Pydantic models with `extra="forbid"` on request bodies; TTL range; pagination caps; header
  patterns for `Idempotency-Key` and `X-Request-ID`; query parameter shapes for the download
  URL (signature must be exactly 43 URL-safe characters).
- Filenames: NFC-normalised, basename only, control characters removed, length-capped;
  `Content-Disposition` built with an ASCII fallback and RFC 5987 encoding so header injection
  is not possible.
- Content types validated against a token/token shape or replaced with
  `application/octet-stream`.

### Transport and headers

- TLS terminated at the platform edge; production refuses a non-HTTPS `PUBLIC_BASE_URL` so
  links are never generated as plaintext.
- `TrustedHostMiddleware` when `TRUSTED_HOSTS` is set; CORS disabled unless configured.
- Downloads: `Content-Disposition: attachment`, `X-Content-Type-Options: nosniff`,
  `Cache-Control: private, no-store`.

### Error handling and logging

- 5xx responses carry only a request id; stack traces go to logs.
- Secrets, keys, signatures and connection strings are never logged.

### Supply chain and runtime

- Multi-stage image on `ubuntu:24.04` LTS with the distribution's Python 3.12; runtime stage has only
  the interpreter, CA certificates and curl, security updates applied at build, non-root uid 10001.
- CI: `pip-audit --strict`, Ruff security rules (`S`), Trivy image scan (HIGH/CRITICAL), gitleaks.
- Dependencies pinned by minimum version in `pyproject.toml`; the Docker build resolves the
  latest compatible set and CI scans it.

## Residual risks and mitigations

| Risk | Status | Mitigation / next step |
|---|---|---|
| Link shared beyond intended recipient | inherent to capability URLs | short TTLs (default 1 h, max 7 d); delete file to revoke; per-link revocation table planned |
| API key leakage | bearer secret | TLS only; rotate via config; move to OIDC tokens with expiry |
| Brute-forcing signatures | 2^256 space | infeasible; monitor `download_failures_total{reason="signature"}` for probing |
| Malicious content served to a browser | served as attachment + nosniff | add scanning worker before `status=available` |
| No rate limiting | v1 gap | edge rate limits (App Platform/Cloudflare) or per-key limiter |
| Disk exhaustion by a single owner | size cap per file only | per-owner quota (sum of `size_bytes`) |
| Ephemeral disk on App Platform | documented | object storage adapter |

## Decision: authentication scope

Authentication is limited to static API keys because the exercise defines file ownership by
user id but no identity provider, registration, or authorisation model beyond "owner". Building
JWT issuance and a users table would invent requirements. In a public production deployment the
`get_current_user` dependency would validate OIDC access tokens from the organisation's IdP and
use the token subject as `owner_id`; nothing else changes.
