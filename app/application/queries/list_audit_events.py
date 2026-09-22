from __future__ import annotations

from app.domain.files.entities import AuditEvent
from app.domain.files.exceptions import FileNotFound
from app.domain.files.ports import AuditRepository, FileRepository


class ListAuditEventsQuery:
    def __init__(self, files: FileRepository, audit: AuditRepository) -> None:
        self._files = files
        self._audit = audit

    def execute(self, owner_id: str, file_id: str, *, limit: int, offset: int) -> tuple[list[AuditEvent], int]:
        if self._files.get_for_owner(file_id, owner_id) is None:
            raise FileNotFound(file_id=file_id)
        return self._audit.list_for_file(file_id, limit=limit, offset=offset)
