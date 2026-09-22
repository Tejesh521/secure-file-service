from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class FileModel(Base):
    __tablename__ = "files"
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key", name="uq_files_owner_idempotency"),
        # Serves the owner's "list my files" query, newest first.
        Index("ix_files_owner_created", "owner_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
