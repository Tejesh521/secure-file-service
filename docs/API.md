# API Reference

Base path: `/v1`. Interactive docs: `/docs`. Spec: `/openapi.json` and
[`openapi/openapi.yaml`](../openapi/openapi.yaml) (regenerate with `make openapi`; CI fails if
stale).

## Conventions

- **Authentication.** Owner endpoints require `X-API-Key: <key>`. The key maps to a `user_id`
  that becomes `owner_id`. The download endpoint takes no credentials; the signature is the
  credential.
- **Correlation.** Send `X-Request-ID` (8–128 chars of `[A-Za-z0-9_.:-]`) to have it preserved;
  otherwise one is generated. It is returned on every response and included in every error body
  and audit event.
- **Errors.** Every non-2xx response:

  ```json
  {
    "error": {
      "code": "VALIDATION_ERROR",
      "message": "Request validation failed.",
      "details": [{"field": "query.limit", "reason": "Input should be greater than or equal to 1"}],
      "request_id": "req_2f8c…"
    }
  }
  ```

- **Pagination.** `limit` (1–100, default 20) and `offset` (0–1,000,000); responses carry
  `page: {limit, offset, total}`. Ordering is newest first with a stable id tiebreak.
- **Timestamps.** RFC 3339 UTC with `Z`.

### Error codes

| HTTP | `code` | When |
|---|---|---|
| 400 | `BAD_REQUEST` | Malformed request outside validation |
| 401 | `UNAUTHORIZED` | Missing or unknown `X-API-Key` (`WWW-Authenticate: ApiKey`) |
| 403 | `LINK_SIGNATURE_INVALID` | Signature does not verify, or unknown `kid` |
| 404 | `FILE_NOT_FOUND` | No such file **or** file belongs to another owner (indistinguishable on purpose) |
| 404 | `NOT_FOUND` | Unknown route |
| 405 | `METHOD_NOT_ALLOWED` | |
| 409 | `IDEMPOTENCY_KEY_REUSED` | Same key, different content |
| 410 | `LINK_EXPIRED` | `exp` is in the past |
| 410 | `FILE_UNAVAILABLE` | File was deleted (link generation or download) |
| 413 | `PAYLOAD_TOO_LARGE` | Body exceeds `MAX_UPLOAD_BYTES` (uploads) or `MAX_REQUEST_BODY_BYTES` (others) |
| 422 | `VALIDATION_ERROR` | Shape/type/range failures; see `details` |
| 422 | `EMPTY_UPLOAD` | Zero-byte file |
| 422 | `INVALID_TTL` | `ttl_seconds` outside `1..MAX_LINK_TTL_SECONDS` |
| 500 | `INTERNAL_ERROR` | Unexpected failure; message hidden, use `request_id` |
| 500 | `STORAGE_INCONSISTENT` | Metadata exists but bytes are missing (alert-worthy) |
| 503 | `SERVICE_UNAVAILABLE` | Database unreachable; safe to retry |

---

## `POST /v1/files` — Upload a private file

Multipart form with a single part named `file`.

Headers: `X-API-Key` (required), `Idempotency-Key` (optional, 1–255 chars `[A-Za-z0-9_.:-]`).

```http
POST /v1/files HTTP/1.1
X-API-Key: dev-key-alice
Idempotency-Key: invoice-2026-09-0042
Content-Type: multipart/form-data; boundary=----b

------b
Content-Disposition: form-data; name="file"; filename="invoice.pdf"
Content-Type: application/pdf

%PDF-1.7 …
------b--
```

Responses:

- `201 Created`, `Location: /v1/files/{id}`

  ```json
  {
    "id": "73d78e58-0d9f-44a8-80e8-8b1c2d205e73",
    "owner_id": "alice",
    "filename": "invoice.pdf",
    "content_type": "application/pdf",
    "size_bytes": 48211,
    "sha256": "265a2bee…",
    "status": "available",
    "created_at": "2026-09-22T21:11:23.614Z",
    "updated_at": "2026-09-22T21:11:23.614Z",
    "deleted_at": null
  }
  ```

- `200 OK` same body: an `Idempotency-Key` matched an earlier upload with identical content.
- `409`, `413`, `422 EMPTY_UPLOAD`, `422 VALIDATION_ERROR`, `401`.

Notes: `filename` is sanitised (basename only, control characters removed, ≤ 255 chars); an
unparseable content type becomes `application/octet-stream`. Neither value is ever used to build a
filesystem path.

---

## `GET /v1/files` — List my files

Query: `limit`, `offset`.

```json
{
  "items": [ { …FileResponse… } ],
  "page": {"limit": 20, "offset": 0, "total": 3}
}
```

---

## `GET /v1/files/{file_id}` — File metadata

Returns `FileResponse` (see above) or `404 FILE_NOT_FOUND`. Deleted files are still returned
with `status: "deleted"` and `deleted_at` set, so owners can see history.

---

## `DELETE /v1/files/{file_id}` — Delete a file

`204 No Content`. Soft-deletes the record, removes the bytes, writes `file.deleted`. Every
outstanding signed link now returns `410 FILE_UNAVAILABLE`. Idempotent: deleting again is `204`.

---

## `POST /v1/files/{file_id}/links` — Generate a signed download link

Body (optional): `{"ttl_seconds": 600}`. Omit for `DEFAULT_LINK_TTL_SECONDS` (3600). Extra
fields are rejected.

```http
POST /v1/files/73d78e58-…/links
X-API-Key: dev-key-alice
Content-Type: application/json

{"ttl_seconds": 600}
```

`201 Created`:

```json
{
  "link_id": "f3103cce-e429-4334-afb9-cdc9da99d6be",
  "file_id": "73d78e58-0d9f-44a8-80e8-8b1c2d205e73",
  "url": "https://files.example.com/v1/download/73d78e58-…?exp=1790115083&lid=f3103cce-…&kid=k1&sig=sFD52_8thnnQBYNQHlRdJuPUBBK0ZTWweGQOKIcnc5g",
  "expires_at": "2026-09-22T22:11:23Z",
  "ttl_seconds": 600,
  "key_id": "k1"
}
```

Side effect: an audit event `link.generated` with `link_id`, `expires_at`, `actor_id`,
`request_id`, `client_ip` and `metadata: {ttl_seconds, key_id}`.

Errors: `404`, `410 FILE_UNAVAILABLE`, `422 INVALID_TTL` (over the configured max),
`422 VALIDATION_ERROR` (≤ 0 or wrong type).

### URL format

| Param | Meaning |
|---|---|
| `exp` | Expiry, Unix epoch seconds (UTC) |
| `lid` | Link id (UUID), correlates downloads with the generation event |
| `kid` | Signing key id from `SIGNING_KEYS` |
| `sig` | `base64url(HMAC-SHA256(secret[kid], "v1\n{file_id}\n{lid}\n{exp}"))` without padding, 43 chars |

---

## `GET /v1/download/{file_id}?exp&lid&kid&sig` — Download via signed link

No credentials. Validation order: signature → expiry → metadata → status → bytes present.

`200 OK` streams the file with:

```
Content-Type: <stored content type>
Content-Length: <size>
Content-Disposition: attachment; filename="invoice.pdf"; filename*=UTF-8''invoice.pdf
Cache-Control: private, no-store
X-Content-Type-Options: nosniff
ETag: "<sha256>"
Accept-Ranges: bytes
```

Errors: `403 LINK_SIGNATURE_INVALID`, `410 LINK_EXPIRED`, `404 FILE_NOT_FOUND`,
`410 FILE_UNAVAILABLE`, `422 VALIDATION_ERROR` (malformed params), `500 STORAGE_INCONSISTENT`.

Each successful download writes `file.downloaded` with the `lid` and the caller's IP.

---

## `GET /v1/files/{file_id}/audit` — Audit trail

Query: `limit`, `offset`. Owner only.

```json
{
  "items": [
    {
      "id": "…",
      "file_id": "73d78e58-…",
      "event_type": "file.downloaded",
      "actor_id": null,
      "link_id": "f3103cce-…",
      "expires_at": null,
      "request_id": "req_8156…",
      "client_ip": "203.0.113.7",
      "metadata": {"key_id": "k1"},
      "created_at": "2026-09-22T21:12:01.004Z"
    },
    {
      "event_type": "link.generated",
      "actor_id": "alice",
      "link_id": "f3103cce-…",
      "expires_at": "2026-09-22T22:11:23Z",
      "metadata": {"ttl_seconds": 600, "key_id": "k1"},
      "…": "…"
    },
    {"event_type": "file.uploaded", "actor_id": "alice", "metadata": {"size_bytes": 48211, "content_type": "application/pdf"}, "…": "…"}
  ],
  "page": {"limit": 20, "offset": 0, "total": 3}
}
```

Event types: `file.uploaded`, `link.generated`, `file.downloaded`, `file.deleted`.

---

## Health and metrics

| Endpoint | 200 when | Body |
|---|---|---|
| `GET /health/live` | process is up | `{"status":"ok","checks":{}}` |
| `GET /health/ready` | startup done, DB answers `SELECT 1`, storage writable | `{"status":"ok","checks":{"startup":"ok","database":"ok","storage":"ok"}}`; `503` with `"unavailable"` entries otherwise |
| `GET /metrics` | always | Prometheus text format |
