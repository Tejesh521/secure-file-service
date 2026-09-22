from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.application.clock import FixedClock
from app.application.commands.delete_file import DeleteFileCommand, DeleteFileHandler
from app.application.context import RequestContext
from app.core.telemetry import Metrics
from app.domain.files.entities import AuditEventType, FileRecord, FileStatus
from app.domain.files.exceptions import FileNotFound
from tests.fixtures.fakes import FakeSession, InMemoryAuditRepository, InMemoryFileRepository, InMemoryStorage

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def make() -> tuple[DeleteFileHandler, InMemoryFileRepository, InMemoryAuditRepository, InMemoryStorage, FakeSession]:
    session, files, audit, storage = (
        FakeSession(),
        InMemoryFileRepository(),
        InMemoryAuditRepository(),
        InMemoryStorage(),
    )
    storage.objects["aa/bb"] = b"x"
    files.records["file-1"] = FileRecord(
        id="file-1",
        owner_id="alice",
        original_filename="a",
        content_type="text/plain",
        size_bytes=1,
        sha256="0" * 64,
        storage_key="aa/bb",
        status=FileStatus.AVAILABLE,
        created_at=NOW,
        updated_at=NOW,
    )
    handler = DeleteFileHandler(session, files, audit, storage, FixedClock(NOW), Metrics())  # type: ignore[arg-type]
    return handler, files, audit, storage, session


def cmd(owner: str = "alice") -> DeleteFileCommand:
    return DeleteFileCommand(owner_id=owner, file_id="file-1", context=RequestContext("req_x"))


def test_delete_soft_deletes_removes_bytes_and_audits() -> None:
    handler, files, audit, storage, session = make()
    handler.execute(cmd())
    record = files.records["file-1"]
    assert record.status is FileStatus.DELETED and record.deleted_at == NOW
    assert storage.objects == {} and storage.deleted == ["aa/bb"]
    assert len(audit.of_type(AuditEventType.FILE_DELETED)) == 1
    assert session.commits == 1


def test_delete_is_idempotent() -> None:
    handler, _, audit, storage, session = make()
    handler.execute(cmd())
    handler.execute(cmd())
    assert len(audit.events) == 1 and session.commits == 1 and len(storage.deleted) == 1


def test_delete_requires_ownership() -> None:
    handler, files, _, storage, _ = make()
    with pytest.raises(FileNotFound):
        handler.execute(cmd(owner="bob"))
    assert files.records["file-1"].status is FileStatus.AVAILABLE and storage.objects


def test_storage_failure_after_commit_is_logged_not_raised() -> None:
    handler, files, _, storage, _ = make()

    def boom(key: str) -> None:
        raise OSError("disk")

    storage.delete = boom  # type: ignore[method-assign]
    handler.execute(cmd())  # does not raise: metadata is authoritative, bytes are reconciled later
    assert files.records["file-1"].status is FileStatus.DELETED
