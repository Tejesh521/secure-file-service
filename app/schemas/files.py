from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.files.entities import AuditEvent, AuditEventType, FileRecord, FileStatus, SignedLink
from app.schemas.common import PageMeta


class FileResponse(BaseModel):
    id: str
    owner_id: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    status: FileStatus
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None

    @classmethod
    def from_entity(cls, record: FileRecord) -> FileResponse:
        return cls(
            id=record.id,
            owner_id=record.owner_id,
            filename=record.original_filename,
            content_type=record.content_type,
            size_bytes=record.size_bytes,
            sha256=record.sha256,
            status=record.status,
            created_at=record.created_at,
            updated_at=record.updated_at,
            deleted_at=record.deleted_at,
        )


class FileListResponse(BaseModel):
    items: list[FileResponse]
    page: PageMeta


class CreateLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ttl_seconds: int | None = Field(
        default=None,
        ge=1,
        le=365 * 24 * 3600,
        description="Lifetime of the link in seconds. Defaults to DEFAULT_LINK_TTL_SECONDS; "
        "capped by MAX_LINK_TTL_SECONDS.",
        examples=[600],
    )


class SignedLinkResponse(BaseModel):
    link_id: str
    file_id: str
    url: str
    expires_at: datetime
    ttl_seconds: int
    key_id: str

    @classmethod
    def from_entity(cls, link: SignedLink) -> SignedLinkResponse:
        return cls(
            link_id=link.link_id,
            file_id=link.file_id,
            url=link.url,
            expires_at=link.expires_at,
            ttl_seconds=link.ttl_seconds,
            key_id=link.key_id,
        )


class AuditEventResponse(BaseModel):
    id: str
    file_id: str
    event_type: AuditEventType
    actor_id: str | None
    link_id: str | None
    expires_at: datetime | None
    request_id: str | None
    client_ip: str | None
    metadata: dict[str, Any]
    created_at: datetime

    @classmethod
    def from_entity(cls, event: AuditEvent) -> AuditEventResponse:
        return cls(
            id=event.id,
            file_id=event.file_id,
            event_type=event.event_type,
            actor_id=event.actor_id,
            link_id=event.link_id,
            expires_at=event.expires_at,
            request_id=event.request_id,
            client_ip=event.client_ip,
            metadata=event.metadata,
            created_at=event.created_at,
        )


class AuditEventListResponse(BaseModel):
    items: list[AuditEventResponse]
    page: PageMeta
