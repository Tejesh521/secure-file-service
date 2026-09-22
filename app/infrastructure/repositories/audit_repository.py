from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.files.entities import AuditEvent, AuditEventType
from app.infrastructure.database.models import AuditEventModel


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _to_entity(model: AuditEventModel) -> AuditEvent:
    created = _aware(model.created_at)
    assert created is not None  # noqa: S101 - column is NOT NULL
    return AuditEvent(
        id=model.id,
        file_id=model.file_id,
        event_type=AuditEventType(model.event_type),
        actor_id=model.actor_id,
        link_id=model.link_id,
        expires_at=_aware(model.expires_at),
        request_id=model.request_id,
        client_ip=model.client_ip,
        metadata=dict(model.metadata_ or {}),
        created_at=created,
    )


class SqlAlchemyAuditRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, event: AuditEvent) -> None:
        self._session.add(
            AuditEventModel(
                id=event.id,
                file_id=event.file_id,
                event_type=event.event_type.value,
                actor_id=event.actor_id,
                link_id=event.link_id,
                expires_at=event.expires_at,
                request_id=event.request_id,
                client_ip=event.client_ip,
                metadata_=event.metadata,
                created_at=event.created_at,
            )
        )

    def list_for_file(self, file_id: str, *, limit: int, offset: int) -> tuple[list[AuditEvent], int]:
        base = select(AuditEventModel).where(AuditEventModel.file_id == file_id)
        total = self._session.scalar(select(func.count()).select_from(base.subquery())) or 0
        rows = self._session.scalars(
            base.order_by(AuditEventModel.created_at.desc(), AuditEventModel.id.desc()).limit(limit).offset(offset)
        ).all()
        return [_to_entity(m) for m in rows], int(total)
