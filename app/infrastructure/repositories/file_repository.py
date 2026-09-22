from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.domain.files.entities import FileRecord, FileStatus
from app.infrastructure.database.models import FileModel


def _aware(dt: datetime) -> datetime:
    # SQLite drops tzinfo; normalise so callers always see UTC-aware values.
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _to_entity(model: FileModel) -> FileRecord:
    return FileRecord(
        id=model.id,
        owner_id=model.owner_id,
        original_filename=model.original_filename,
        content_type=model.content_type,
        size_bytes=model.size_bytes,
        sha256=model.sha256,
        storage_key=model.storage_key,
        status=FileStatus(model.status),
        idempotency_key=model.idempotency_key,
        created_at=_aware(model.created_at),
        updated_at=_aware(model.updated_at),
        deleted_at=_aware(model.deleted_at) if model.deleted_at else None,
    )


class SqlAlchemyFileRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: FileRecord) -> None:
        self._session.add(
            FileModel(
                id=record.id,
                owner_id=record.owner_id,
                original_filename=record.original_filename,
                content_type=record.content_type,
                size_bytes=record.size_bytes,
                sha256=record.sha256,
                storage_key=record.storage_key,
                status=record.status.value,
                idempotency_key=record.idempotency_key,
                created_at=record.created_at,
                updated_at=record.updated_at,
                deleted_at=record.deleted_at,
            )
        )
        # Flush now so the row exists before audit events referencing it are
        # inserted in the same transaction. Without a mapped relationship the
        # unit of work does not order inserts across tables, and PostgreSQL
        # enforces the foreign key immediately.
        self._session.flush()

    def get(self, file_id: str) -> FileRecord | None:
        model = self._session.get(FileModel, file_id)
        return _to_entity(model) if model else None

    def get_for_owner(self, file_id: str, owner_id: str) -> FileRecord | None:
        stmt = select(FileModel).where(FileModel.id == file_id, FileModel.owner_id == owner_id)
        model = self._session.scalars(stmt).first()
        return _to_entity(model) if model else None

    def find_by_idempotency_key(self, owner_id: str, key: str) -> FileRecord | None:
        stmt = select(FileModel).where(FileModel.owner_id == owner_id, FileModel.idempotency_key == key)
        model = self._session.scalars(stmt).first()
        return _to_entity(model) if model else None

    def list_for_owner(self, owner_id: str, *, limit: int, offset: int) -> tuple[list[FileRecord], int]:
        base = select(FileModel).where(FileModel.owner_id == owner_id)
        total = self._session.scalar(select(func.count()).select_from(base.subquery())) or 0
        rows = self._session.scalars(
            base.order_by(FileModel.created_at.desc(), FileModel.id.desc()).limit(limit).offset(offset)
        ).all()
        return [_to_entity(m) for m in rows], int(total)

    def set_status(self, file_id: str, status: FileStatus, at: datetime) -> None:
        values: dict[str, object] = {"status": status.value, "updated_at": at}
        if status == FileStatus.DELETED:
            values["deleted_at"] = at
        self._session.execute(update(FileModel).where(FileModel.id == file_id).values(**values))
