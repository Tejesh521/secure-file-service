"""Domain entities. Plain dataclasses, independent of the database or HTTP."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class FileStatus(StrEnum):
    AVAILABLE = "available"
    DELETED = "deleted"


class AuditEventType(StrEnum):
    FILE_UPLOADED = "file.uploaded"
    LINK_GENERATED = "link.generated"
    FILE_DOWNLOADED = "file.downloaded"
    FILE_DELETED = "file.deleted"


@dataclass(slots=True)
class FileRecord:
    id: str
    owner_id: str
    original_filename: str
    content_type: str
    size_bytes: int
    sha256: str
    storage_key: str
    status: FileStatus
    created_at: datetime
    updated_at: datetime
    idempotency_key: str | None = None
    deleted_at: datetime | None = None

    @property
    def is_available(self) -> bool:
        return self.status == FileStatus.AVAILABLE


@dataclass(slots=True)
class AuditEvent:
    id: str
    file_id: str
    event_type: AuditEventType
    created_at: datetime
    actor_id: str | None = None
    link_id: str | None = None
    expires_at: datetime | None = None
    request_id: str | None = None
    client_ip: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SignedLink:
    link_id: str
    file_id: str
    url: str
    expires_at: datetime
    key_id: str
    ttl_seconds: int


@dataclass(frozen=True, slots=True)
class StoredObject:
    """Result of writing bytes to storage."""

    storage_key: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class DownloadTarget:
    """Everything the HTTP layer needs to stream a file to a client."""

    path: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
