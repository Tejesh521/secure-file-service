from __future__ import annotations

from app.domain.files.entities import FileRecord
from app.domain.files.exceptions import FileNotFound
from app.domain.files.ports import FileRepository


class GetFileQuery:
    def __init__(self, files: FileRepository) -> None:
        self._files = files

    def execute(self, owner_id: str, file_id: str) -> FileRecord:
        record = self._files.get_for_owner(file_id, owner_id)
        if record is None:
            # Same response whether the file does not exist or belongs to someone
            # else: owners cannot probe for other users' file ids.
            raise FileNotFound(file_id=file_id)
        return record
