"""Resolve a signed URL into a file to serve.

Order of checks is deliberate: signature first (cheap, no I/O, fails closed for
garbage), then expiry, then database lookup, then storage existence. A request
with a bad signature never touches the database.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.application.context import RequestContext
from app.core.security import SignatureError, UrlSigner
from app.core.telemetry import Metrics
from app.domain.files.entities import AuditEvent, AuditEventType, DownloadTarget
from app.domain.files.exceptions import (
    FileNotFound,
    FileUnavailable,
    LinkExpired,
    LinkSignatureInvalid,
    StorageInconsistent,
)
from app.domain.files.ports import AuditRepository, Clock, FileRepository, FileStorage
from app.domain.files.services import is_expired

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SignedRequest:
    file_id: str
    expires_at: int
    link_id: str
    key_id: str
    signature: str


class DownloadService:
    def __init__(
        self,
        session: Session,
        files: FileRepository,
        audit: AuditRepository,
        storage: FileStorage,
        signer: UrlSigner,
        clock: Clock,
        metrics: Metrics,
    ) -> None:
        self._session = session
        self._files = files
        self._audit = audit
        self._storage = storage
        self._signer = signer
        self._clock = clock
        self._metrics = metrics

    def resolve(self, req: SignedRequest, context: RequestContext) -> DownloadTarget:
        try:
            self._signer.verify(req.file_id, req.link_id, req.expires_at, req.key_id, req.signature)
        except SignatureError as exc:
            self._metrics.download_failures_total.labels(reason="signature").inc()
            raise LinkSignatureInvalid(file_id=req.file_id, reason=type(exc).__name__) from exc

        now = self._clock.now()
        if is_expired(req.expires_at, now):
            self._metrics.download_failures_total.labels(reason="expired").inc()
            raise LinkExpired(file_id=req.file_id, expires_at=req.expires_at)

        record = self._files.get(req.file_id)
        if record is None:
            self._metrics.download_failures_total.labels(reason="not_found").inc()
            raise FileNotFound(file_id=req.file_id)
        if not record.is_available:
            self._metrics.download_failures_total.labels(reason="unavailable").inc()
            raise FileUnavailable(file_id=req.file_id)
        if not self._storage.exists(record.storage_key):
            self._metrics.download_failures_total.labels(reason="storage_missing").inc()
            logger.error("stored object missing", extra={"file_id": record.id, "storage_key": record.storage_key})
            raise StorageInconsistent(file_id=record.id)

        self._audit.add(
            AuditEvent(
                id=str(uuid.uuid4()),
                file_id=record.id,
                event_type=AuditEventType.FILE_DOWNLOADED,
                link_id=req.link_id,
                request_id=context.request_id,
                client_ip=context.client_ip,
                metadata={"key_id": req.key_id},
                created_at=now,
            )
        )
        self._session.commit()
        self._metrics.downloads_total.inc()
        logger.info("download served", extra={"file_id": record.id, "link_id": req.link_id})
        return DownloadTarget(
            path=self._storage.path_for(record.storage_key),
            filename=record.original_filename,
            content_type=record.content_type,
            size_bytes=record.size_bytes,
            sha256=record.sha256,
        )
