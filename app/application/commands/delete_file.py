"""Soft-delete a file and remove its bytes. Existing signed links stop working."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.application.context import RequestContext
from app.core.telemetry import Metrics
from app.domain.files.entities import AuditEvent, AuditEventType, FileStatus
from app.domain.files.exceptions import FileNotFound
from app.domain.files.ports import AuditRepository, Clock, FileRepository, FileStorage
from app.domain.files.state import transition

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DeleteFileCommand:
    owner_id: str
    file_id: str
    context: RequestContext


class DeleteFileHandler:
    def __init__(
        self,
        session: Session,
        files: FileRepository,
        audit: AuditRepository,
        storage: FileStorage,
        clock: Clock,
        metrics: Metrics,
    ) -> None:
        self._session = session
        self._files = files
        self._audit = audit
        self._storage = storage
        self._clock = clock
        self._metrics = metrics

    def execute(self, cmd: DeleteFileCommand) -> None:
        record = self._files.get_for_owner(cmd.file_id, cmd.owner_id)
        if record is None:
            raise FileNotFound(file_id=cmd.file_id)
        if record.status == FileStatus.DELETED:
            return  # idempotent: deleting twice is a no-op
        now = self._clock.now()
        new_status = transition(record.status, FileStatus.DELETED)
        self._files.set_status(record.id, new_status, now)
        self._audit.add(
            AuditEvent(
                id=str(uuid.uuid4()),
                file_id=record.id,
                event_type=AuditEventType.FILE_DELETED,
                actor_id=cmd.owner_id,
                request_id=cmd.context.request_id,
                client_ip=cmd.context.client_ip,
                created_at=now,
            )
        )
        # Commit the metadata change first: a deleted record with orphaned bytes
        # is recoverable by a reconciliation sweep; the reverse (bytes gone,
        # metadata says available) would be a serving error.
        self._session.commit()
        try:
            self._storage.delete(record.storage_key)
        except OSError:
            logger.exception("failed to delete stored object", extra={"file_id": record.id})
        self._metrics.files_deleted_total.inc()
        logger.info("file deleted", extra={"file_id": record.id})
