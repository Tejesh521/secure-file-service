"""Upload a file: stream to private storage, persist metadata, record audit."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import BinaryIO

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.context import RequestContext
from app.core.telemetry import Metrics
from app.domain.files.entities import AuditEvent, AuditEventType, FileRecord, FileStatus, StoredObject
from app.domain.files.exceptions import IdempotencyConflict
from app.domain.files.ports import AuditRepository, Clock, FileRepository, FileStorage
from app.domain.files.services import normalize_content_type, sanitize_filename

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UploadFileCommand:
    owner_id: str
    filename: str | None
    content_type: str | None
    stream: BinaryIO
    idempotency_key: str | None
    context: RequestContext


@dataclass(frozen=True, slots=True)
class UploadFileResult:
    file: FileRecord
    replayed: bool  # True when an Idempotency-Key matched an earlier upload


class UploadFileHandler:
    def __init__(
        self,
        session: Session,
        files: FileRepository,
        audit: AuditRepository,
        storage: FileStorage,
        clock: Clock,
        metrics: Metrics,
        *,
        max_upload_bytes: int,
    ) -> None:
        self._session = session
        self._files = files
        self._audit = audit
        self._storage = storage
        self._clock = clock
        self._metrics = metrics
        self._max = max_upload_bytes

    def execute(self, cmd: UploadFileCommand) -> UploadFileResult:
        # 1. Stream to storage first. We need the content hash to decide whether an
        #    idempotent replay carries the same payload.
        try:
            stored = self._storage.write(cmd.stream, max_bytes=self._max)
        except Exception as exc:
            self._metrics.upload_failures_total.labels(reason=type(exc).__name__).inc()
            raise

        if cmd.idempotency_key:
            existing = self._files.find_by_idempotency_key(cmd.owner_id, cmd.idempotency_key)
            if existing is not None:
                return self._replay(existing, stored)

        now = self._clock.now()
        record = FileRecord(
            id=str(uuid.uuid4()),
            owner_id=cmd.owner_id,
            original_filename=sanitize_filename(cmd.filename),
            content_type=normalize_content_type(cmd.content_type),
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
            storage_key=stored.storage_key,
            status=FileStatus.AVAILABLE,
            idempotency_key=cmd.idempotency_key,
            created_at=now,
            updated_at=now,
        )
        try:
            self._files.add(record)
            self._audit.add(
                AuditEvent(
                    id=str(uuid.uuid4()),
                    file_id=record.id,
                    event_type=AuditEventType.FILE_UPLOADED,
                    actor_id=cmd.owner_id,
                    request_id=cmd.context.request_id,
                    client_ip=cmd.context.client_ip,
                    metadata={"size_bytes": record.size_bytes, "content_type": record.content_type},
                    created_at=now,
                )
            )
            self._session.commit()
        except IntegrityError:
            # Two concurrent requests with the same Idempotency-Key raced; the
            # unique constraint is the arbiter. Fall back to replay semantics.
            self._session.rollback()
            if cmd.idempotency_key:
                existing = self._files.find_by_idempotency_key(cmd.owner_id, cmd.idempotency_key)
                if existing is not None:
                    return self._replay(existing, stored)
            self._storage.delete(stored.storage_key)
            raise
        except Exception:
            self._session.rollback()
            # Never leave orphaned bytes behind when metadata was not persisted.
            self._storage.delete(stored.storage_key)
            self._metrics.upload_failures_total.labels(reason="persistence_error").inc()
            raise

        self._metrics.files_uploaded_total.inc()
        self._metrics.upload_bytes_total.inc(stored.size_bytes)
        logger.info(
            "file uploaded",
            extra={"file_id": record.id, "size_bytes": record.size_bytes, "owner_id": record.owner_id},
        )
        return UploadFileResult(file=record, replayed=False)

    def _replay(self, existing: FileRecord, stored: StoredObject) -> UploadFileResult:
        # The new bytes are redundant either way; remove them before deciding.
        self._storage.delete(stored.storage_key)
        if existing.sha256 != stored.sha256 or existing.size_bytes != stored.size_bytes:
            self._metrics.upload_failures_total.labels(reason="idempotency_conflict").inc()
            raise IdempotencyConflict(file_id=existing.id)
        logger.info("idempotent upload replayed", extra={"file_id": existing.id})
        return UploadFileResult(file=existing, replayed=True)
