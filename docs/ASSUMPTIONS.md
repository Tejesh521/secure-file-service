# Assumptions

Recorded before and during implementation. Each one is a talking point; if any is wrong, the
"if wrong" column says what would change.

| # | Assumption | Basis | If wrong |
|---|---|---|---|
| A1 | Files are at most 100 MiB and typically far smaller | Typical document/media sharing; brief gives no size | Raise `MAX_UPLOAD_BYTES`; move to resumable/multipart upload protocol; object storage sooner |
| A2 | "Local file system" is a hard requirement of the exercise, not a preference | Brief wording | Object storage adapter becomes v1 |
| A3 | A single API instance with a persistent volume is acceptable for v1 | Local disk is per instance | Swap storage adapter before scaling out |
| A4 | Owner identity is a user id supplied by the caller's credential; no user registration or IdP exists | Brief defines no identity system | Replace `get_current_user` with token validation |
| A5 | Whoever holds a signed URL is authorised for the TTL granted | Definition of a capability URL | Add per-link revocation and/or download caps |
| A6 | Links do not need individual revocation in v1; deleting the file is sufficient | Not in brief | Add `revoked_links` table checked by `lid` |
| A7 | Server clocks are NTP-synchronised within seconds | Managed platform | Add small clock skew tolerance on verification |
| A8 | Clients may retry uploads after timeouts | Standard HTTP client behaviour | Idempotency-Key support is already present |
| A9 | Audit events must be durable and written with the action they describe | "must record an audit event every time a signed link is generated" | Already transactional |
| A10 | Uploaded content is trusted enough to store verbatim; scanning is out of scope | Brief silent on content safety | Add scanning worker and `quarantined` state |
| A11 | Read/write ratios are modest; no caching is required | Development-scale expectation | Add CDN/presigned direct downloads |
| A12 | PostgreSQL is available as a managed dependency | DigitalOcean offers it | SQLite would break concurrency guarantees; not acceptable |
| A13 | HTTP range requests and resumable downloads are not required | Not in brief | Serve ranges via Starlette `FileResponse` range support |
| A14 | The deployment target is DigitalOcean App Platform | Interview context | Dockerfile is platform-neutral |
| A15 | Owners may see deleted files in listings/metadata (history) but not download them | Audit-friendly | Filter `status=available` in list query |
