"""Repositories against real PostgreSQL: constraints, ordering, cascade, JSONB."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.files.entities import AuditEvent, AuditEventType, FileRecord, FileStatus
from app.infrastructure.repositories.audit_repository import SqlAlchemyAuditRepository
from app.infrastructure.repositories.file_repository import SqlAlchemyFileRepository

pytestmark = pytest.mark.integration
NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def record(file_id: str, owner: str = "alice", *, key: str | None = None, at: datetime = NOW) -> FileRecord:
    return FileRecord(
        id=file_id,
        owner_id=owner,
        original_filename=f"{file_id}.txt",
        content_type="text/plain",
        size_bytes=3,
        sha256="a" * 64,
        storage_key=f"aa/{file_id}",
        status=FileStatus.AVAILABLE,
        idempotency_key=key,
        created_at=at,
        updated_at=at,
    )


def test_add_get_roundtrip_preserves_timezone(pg_session: Session) -> None:
    repo = SqlAlchemyFileRepository(pg_session)
    repo.add(record("f1"))
    pg_session.commit()
    pg_session.expunge_all()
    got = repo.get("f1")
    assert got is not None
    assert got.created_at == NOW and got.created_at.tzinfo is not None
    assert got.status is FileStatus.AVAILABLE
    assert repo.get_for_owner("f1", "alice") is not None
    assert repo.get_for_owner("f1", "bob") is None
    assert repo.get("missing") is None


def test_idempotency_key_unique_per_owner(pg_session: Session) -> None:
    repo = SqlAlchemyFileRepository(pg_session)
    repo.add(record("f1", key="k"))
    repo.add(record("f2", owner="bob", key="k"))  # same key, different owner: fine
    pg_session.commit()
    with pytest.raises(IntegrityError) as exc:
        repo.add(record("f3", key="k"))
        pg_session.commit()
    assert "uq_files_owner_idempotency" in str(exc.value)
    pg_session.rollback()
    assert repo.find_by_idempotency_key("alice", "k") is not None
    assert repo.find_by_idempotency_key("alice", "other") is None


def test_null_idempotency_keys_do_not_collide(pg_session: Session) -> None:
    repo = SqlAlchemyFileRepository(pg_session)
    repo.add(record("f1"))
    repo.add(record("f2"))
    pg_session.commit()
    assert repo.list_for_owner("alice", limit=10, offset=0)[1] == 2


def test_storage_key_unique(pg_session: Session) -> None:
    repo = SqlAlchemyFileRepository(pg_session)
    repo.add(record("f1"))
    dup = record("f2")
    dup.storage_key = "aa/f1"
    with pytest.raises(IntegrityError):
        repo.add(dup)
        pg_session.commit()
    pg_session.rollback()


def test_list_newest_first_with_stable_tiebreak_and_total(pg_session: Session) -> None:
    repo = SqlAlchemyFileRepository(pg_session)
    for i in range(5):
        repo.add(record(f"f{i}", at=NOW + timedelta(seconds=i)))
    repo.add(record("same-a", at=NOW + timedelta(seconds=10)))
    repo.add(record("same-b", at=NOW + timedelta(seconds=10)))
    repo.add(record("bob1", owner="bob"))
    pg_session.commit()

    items, total = repo.list_for_owner("alice", limit=3, offset=0)
    assert total == 7
    assert [r.id for r in items] == ["same-b", "same-a", "f4"]
    items, _ = repo.list_for_owner("alice", limit=3, offset=6)
    assert [r.id for r in items] == ["f0"]
    assert repo.list_for_owner("nobody", limit=3, offset=0) == ([], 0)


def test_set_status_marks_deleted(pg_session: Session) -> None:
    repo = SqlAlchemyFileRepository(pg_session)
    repo.add(record("f1"))
    pg_session.commit()
    later = NOW + timedelta(minutes=1)
    repo.set_status("f1", FileStatus.DELETED, later)
    pg_session.commit()
    pg_session.expunge_all()
    got = repo.get("f1")
    assert got is not None and got.status is FileStatus.DELETED
    assert got.deleted_at == later and got.updated_at == later


def test_audit_events_jsonb_ordering_and_cascade(pg_session: Session, pg_engine) -> None:  # type: ignore[no-untyped-def]
    files, audit = SqlAlchemyFileRepository(pg_session), SqlAlchemyAuditRepository(pg_session)
    files.add(record("f1"))
    for i, kind in enumerate(
        [AuditEventType.FILE_UPLOADED, AuditEventType.LINK_GENERATED, AuditEventType.FILE_DOWNLOADED]
    ):
        audit.add(
            AuditEvent(
                id=f"e{i}",
                file_id="f1",
                event_type=kind,
                actor_id="alice" if kind != AuditEventType.FILE_DOWNLOADED else None,
                link_id="l1" if kind != AuditEventType.FILE_UPLOADED else None,
                expires_at=NOW + timedelta(hours=1) if kind == AuditEventType.LINK_GENERATED else None,
                request_id=f"req_{i}",
                client_ip="10.0.0.1",
                metadata={"i": i, "nested": {"ok": True}},
                created_at=NOW + timedelta(seconds=i),
            )
        )
    pg_session.commit()
    pg_session.expunge_all()

    events, total = audit.list_for_file("f1", limit=10, offset=0)
    assert total == 3
    assert [e.event_type for e in events] == [
        AuditEventType.FILE_DOWNLOADED,
        AuditEventType.LINK_GENERATED,
        AuditEventType.FILE_UPLOADED,
    ]
    link_event = events[1]
    assert link_event.metadata == {"i": 1, "nested": {"ok": True}}
    assert link_event.expires_at == NOW + timedelta(hours=1)

    with pg_engine.connect() as conn:
        col_type = conn.execute(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name='audit_events' AND column_name='metadata'"
            )
        ).scalar()
        assert col_type == "jsonb"
        # Deleting the parent row removes its audit trail (FK ON DELETE CASCADE).
        conn.execute(text("DELETE FROM files WHERE id='f1'"))
        conn.commit()
        assert conn.execute(text("SELECT count(*) FROM audit_events")).scalar() == 0


def test_indexes_exist_for_hot_queries(pg_engine) -> None:  # type: ignore[no-untyped-def]
    with pg_engine.connect() as conn:
        names = {row[0] for row in conn.execute(text("SELECT indexname FROM pg_indexes WHERE schemaname='public'"))}
    assert {"ix_files_owner_created", "ix_audit_events_file_created", "uq_files_owner_idempotency"} <= names
