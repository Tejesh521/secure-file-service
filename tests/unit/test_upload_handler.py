"""UploadFileHandler with in-memory adapters."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.application.clock import FixedClock
from app.application.commands.upload_file import UploadFileCommand, UploadFileHandler
from app.application.context import RequestContext
from app.core.telemetry import Metrics
from app.domain.files.entities import AuditEventType, FileStatus
from app.domain.files.exceptions import EmptyUpload, IdempotencyConflict, UploadTooLarge
from tests.fixtures.fakes import FakeSession, InMemoryAuditRepository, InMemoryFileRepository, InMemoryStorage, stream

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
CTX = RequestContext(request_id="req_1", client_ip="10.0.0.1")


class Harness:
    def __init__(self, max_upload_bytes: int = 1024) -> None:
        self.session = FakeSession()
        self.files = InMemoryFileRepository()
        self.audit = InMemoryAuditRepository()
        self.storage = InMemoryStorage()
        self.metrics = Metrics()
        self.handler = UploadFileHandler(
            self.session,  # type: ignore[arg-type]
            self.files,
            self.audit,
            self.storage,
            FixedClock(NOW),
            self.metrics,
            max_upload_bytes=max_upload_bytes,
        )

    def upload(self, data: bytes = b"hello", key: str | None = None, filename: str = "a.txt") -> object:
        return self.handler.execute(
            UploadFileCommand(
                owner_id="alice",
                filename=filename,
                content_type="text/plain",
                stream=stream(data),
                idempotency_key=key,
                context=CTX,
            )
        )


def test_upload_persists_metadata_bytes_and_audit() -> None:
    h = Harness()
    result = h.upload(b"hello", filename="../evil.txt")
    record = result.file  # type: ignore[attr-defined]
    assert result.replayed is False  # type: ignore[attr-defined]
    assert record.owner_id == "alice"
    assert record.original_filename == "evil.txt"
    assert record.size_bytes == 5
    assert record.status is FileStatus.AVAILABLE
    assert record.created_at == NOW
    assert h.storage.objects[record.storage_key] == b"hello"
    assert h.files.records[record.id] is record
    assert h.session.commits == 1
    events = h.audit.of_type(AuditEventType.FILE_UPLOADED)
    assert len(events) == 1 and events[0].request_id == "req_1" and events[0].client_ip == "10.0.0.1"
    assert events[0].metadata == {"size_bytes": 5, "content_type": "text/plain"}


def test_idempotent_replay_returns_existing_and_discards_new_bytes() -> None:
    h = Harness()
    first = h.upload(b"same", key="k-1")
    second = h.upload(b"same", key="k-1")
    assert second.replayed is True  # type: ignore[attr-defined]
    assert second.file.id == first.file.id  # type: ignore[attr-defined]
    assert len(h.storage.objects) == 1  # redundant copy removed
    assert len(h.files.records) == 1
    assert len(h.audit.events) == 1  # no second upload event


def test_idempotency_key_with_different_content_conflicts() -> None:
    h = Harness()
    h.upload(b"one", key="k-1")
    with pytest.raises(IdempotencyConflict):
        h.upload(b"two", key="k-1")
    assert len(h.storage.objects) == 1


def test_same_key_different_owner_is_independent() -> None:
    h = Harness()
    h.upload(b"one", key="k-1")
    result = h.handler.execute(
        UploadFileCommand(
            owner_id="bob", filename="b", content_type=None, stream=stream(b"two"), idempotency_key="k-1", context=CTX
        )
    )
    assert result.replayed is False
    assert len(h.files.records) == 2


def test_race_on_unique_constraint_falls_back_to_replay() -> None:
    """Simulate two concurrent requests: the lookup misses but the insert collides."""
    h = Harness()
    first = h.upload(b"same", key="k-1")
    # Make the pre-insert lookup miss so the handler attempts an insert that violates the constraint.
    original = h.files.find_by_idempotency_key
    calls = {"n": 0}

    def flaky(owner_id: str, key: str):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return None if calls["n"] == 1 else original(owner_id, key)

    h.files.find_by_idempotency_key = flaky  # type: ignore[method-assign]
    second = h.upload(b"same", key="k-1")
    assert second.replayed is True  # type: ignore[attr-defined]
    assert second.file.id == first.file.id  # type: ignore[attr-defined]
    assert h.session.rollbacks == 1
    assert len(h.storage.objects) == 1


def test_persistence_failure_removes_orphaned_bytes() -> None:
    h = Harness()
    h.files.fail_on_add = RuntimeError("db down")
    with pytest.raises(RuntimeError):
        h.upload(b"data")
    assert h.storage.objects == {}
    assert h.session.rollbacks == 1
    assert h.session.commits == 0


def test_empty_and_oversized_uploads_rejected_before_persistence() -> None:
    h = Harness(max_upload_bytes=4)
    with pytest.raises(EmptyUpload):
        h.upload(b"")
    with pytest.raises(UploadTooLarge):
        h.upload(b"12345")
    assert h.files.records == {} and h.audit.events == []
