from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base

JsonType = JSON().with_variant(JSONB(), "postgresql")


class AuditEventModel(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        # Serves "show me the audit trail for this file", newest first.
        Index("ix_audit_events_file_created", "file_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    file_id: Mapped[str] = mapped_column(String(36), ForeignKey("files.id", ondelete="CASCADE"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    link_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JsonType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
