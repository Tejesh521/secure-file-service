"""Ports (interfaces) the domain and application layers depend on.

Infrastructure provides the adapters. Keeping these as ``Protocol`` classes means
tests can substitute in-memory fakes and the domain never imports SQLAlchemy.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import BinaryIO, Protocol

from app.domain.files.entities import AuditEvent, FileRecord, FileStatus, StoredObject


class Clock(Protocol):
    def now(self) -> datetime: ...


class FileRepository(Protocol):
    def add(self, record: FileRecord) -> None: ...

    def get(self, file_id: str) -> FileRecord | None: ...

    def get_for_owner(self, file_id: str, owner_id: str) -> FileRecord | None: ...

    def find_by_idempotency_key(self, owner_id: str, key: str) -> FileRecord | None: ...

    def list_for_owner(self, owner_id: str, *, limit: int, offset: int) -> tuple[list[FileRecord], int]: ...

    def set_status(self, file_id: str, status: FileStatus, at: datetime) -> None: ...


class AuditRepository(Protocol):
    def add(self, event: AuditEvent) -> None: ...

    def list_for_file(self, file_id: str, *, limit: int, offset: int) -> tuple[list[AuditEvent], int]: ...


class FileStorage(Protocol):
    def write(self, stream: BinaryIO, *, max_bytes: int) -> StoredObject: ...

    def path_for(self, storage_key: str) -> str: ...

    def exists(self, storage_key: str) -> bool: ...

    def delete(self, storage_key: str) -> None: ...

    def open(self, storage_key: str) -> Iterator[bytes]: ...
