# Known Limitations

Stated plainly so they can be discussed, not discovered.

1. **No per-link revocation.** A link is valid until `exp` unless the file is deleted or the
   signing key is removed from the ring. Generation is audited per `link_id`, so adding a
   revocation check is a small, backward-compatible change.
2. **Single instance because storage is local.** Bytes live on the instance's disk. Two
   instances would each see only their own files. On App Platform the container disk is also
   ephemeral without an attached volume, so a redeploy loses files. Acceptable for a demo; the
   object-storage adapter is the first production step.
3. **No rate limiting.** Neither per API key nor per IP. Signature brute force is infeasible,
   but abusive traffic could consume CPU and disk. Edge rate limiting or a per-key limiter is
   the fix.
4. **No content scanning.** Files are stored and served verbatim (as attachments with
   `nosniff`). A scanning worker with a `quarantined` state is the evolution path.
5. **API-key authentication only.** Keys are static configuration; rotation needs a redeploy;
   there are no scopes or per-user quotas.
6. **No HTTP range requests / resumable downloads or uploads.** Large-file UX suffers.
7. **No per-owner storage quota.** Only a per-file cap. A single key could fill the disk.
8. **Idempotent replays still transfer the bytes** before being discarded.
9. **Audit table grows unbounded.** No partitioning or retention job yet; indexed for the
   per-file query, not for global analytics.
10. **Orphaned bytes need a sweep.** Persistence failures clean up inline, but a crash between
    the storage write and the database commit could leave an object with no row; there is no
    scheduled reconciliation yet.
11. **Performance validated only at development scale** with an in-process latency guard, not a
    load test against a deployment.
12. **No alerting integration configured** in the timebox; alert rules are documented, not
    provisioned.
13. **Image build verified only in CI.** The development environment has no Docker daemon, so
    the Ubuntu 24.04 image is built, checked for non-root and smoke-tested in the `docker` CI
    job rather than locally.
14. **Owner listing includes deleted files.** Intentional for history, but there is no
    `status` filter parameter yet.
