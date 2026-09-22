from __future__ import annotations

from app.domain.files.entities import FileRecord
from app.domain.files.ports import FileRepository


class ListFilesQuery:
    def __init__(self, files: FileRepository) -> None:
        self._files = files

    def execute(self, owner_id: str, *, limit: int, offset: int) -> tuple[list[FileRecord], int]:
        return self._files.list_for_owner(owner_id, limit=limit, offset=offset)
