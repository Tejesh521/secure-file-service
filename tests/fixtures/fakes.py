"""In-memory adapters implementing the domain ports, for fast unit tests."""

from __future__ import annotations

import hashlib
import io
import uuid
from collections.abc import Iterator
from datetime import datetime
from typing import BinaryIO

from app.domain.files.entities import AuditEvent, FileRecord, FileStatus, StoredObject
from app.domain.files.exceptions import EmptyUpload, UploadTooLarge


class FakeSession:
    """Records commit/rollback calls so tests can assert transaction behaviour."""

    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class InMemoryFileRepository:
    def __init__(self) -> None:
        self.records: dict[str, FileRecord] = {}
        self.get_calls = 0
        self.fail_on_add: Exception | None = None

    def add(self, record: FileRecord) -> None:
        if self.fail_on_add is not None:
            raise self.fail_on_add
        for existing in self.records.values():
            if (
                record.idempotency_key
                and existing.owner_id == record.owner_id
                and existing.idempotency_key == record.idempotency_key
            ):
                from sqlalchemy.exc import IntegrityError

                raise IntegrityError("INSERT", {}, Exception("uq_files_owner_idempotency"))
        self.records[record.id] = record

    def get(self, file_id: str) -> FileRecord | None:
        self.get_calls += 1
        return self.records.get(file_id)

    def get_for_owner(self, file_id: str, owner_id: str) -> FileRecord | None:
        record = self.records.get(file_id)
        return record if record and record.owner_id == owner_id else None

    def find_by_idempotency_key(self, owner_id: str, key: str) -> FileRecord | None:
        for record in self.records.values():
            if record.owner_id == owner_id and record.idempotency_key == key:
                return record
        return None

    def list_for_owner(self, owner_id: str, *, limit: int, offset: int) -> tuple[list[FileRecord], int]:
        mine = sorted(
            (r for r in self.records.values() if r.owner_id == owner_id), key=lambda r: r.created_at, reverse=True
        )
        return mine[offset : offset + limit], len(mine)

    def set_status(self, file_id: str, status: FileStatus, at: datetime) -> None:
        record = self.records[file_id]
        record.status = status
        record.updated_at = at
        if status == FileStatus.DELETED:
            record.deleted_at = at


class InMemoryAuditRepository:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def add(self, event: AuditEvent) -> None:
        self.events.append(event)

    def list_for_file(self, file_id: str, *, limit: int, offset: int) -> tuple[list[AuditEvent], int]:
        matching = sorted((e for e in self.events if e.file_id == file_id), key=lambda e: e.created_at, reverse=True)
        return matching[offset : offset + limit], len(matching)

    def of_type(self, event_type: str) -> list[AuditEvent]:
        return [e for e in self.events if e.event_type == event_type]


class InMemoryStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def write(self, stream: BinaryIO, *, max_bytes: int) -> StoredObject:
        data = stream.read()
        if len(data) > max_bytes:
            raise UploadTooLarge(max_bytes=max_bytes)
        if not data:
            raise EmptyUpload()
        key = uuid.uuid4().hex
        self.objects[key] = data
        return StoredObject(storage_key=key, size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())

    def path_for(self, storage_key: str) -> str:
        return f"/fake/{storage_key}"

    def exists(self, storage_key: str) -> bool:
        return storage_key in self.objects

    def delete(self, storage_key: str) -> None:
        self.deleted.append(storage_key)
        self.objects.pop(storage_key, None)

    def open(self, storage_key: str) -> Iterator[bytes]:
        yield self.objects[storage_key]


def stream(data: bytes) -> BinaryIO:
    return io.BytesIO(data)
