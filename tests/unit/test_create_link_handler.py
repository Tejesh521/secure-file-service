"""CreateSignedLinkHandler: link shape, audit event, ownership and TTL rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from app.application.clock import FixedClock
from app.application.commands.create_signed_link import CreateSignedLinkCommand, CreateSignedLinkHandler
from app.application.context import RequestContext
from app.core.security import UrlSigner
from app.core.telemetry import Metrics
from app.domain.files.entities import AuditEventType, FileRecord, FileStatus
from app.domain.files.exceptions import FileNotFound, FileUnavailable, InvalidTTL
from tests.fixtures.fakes import FakeSession, InMemoryAuditRepository, InMemoryFileRepository

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
SIGNER = UrlSigner({"k1": b"0123456789abcdef0123456789abcdef"}, "k1")


def record(owner: str = "alice", status: FileStatus = FileStatus.AVAILABLE) -> FileRecord:
    return FileRecord(
        id="file-1",
        owner_id=owner,
        original_filename="a.txt",
        content_type="text/plain",
        size_bytes=1,
        sha256="0" * 64,
        storage_key="aa/bb",
        status=status,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.fixture
def parts() -> tuple[CreateSignedLinkHandler, InMemoryFileRepository, InMemoryAuditRepository, FakeSession]:
    session, files, audit = FakeSession(), InMemoryFileRepository(), InMemoryAuditRepository()
    handler = CreateSignedLinkHandler(
        session,  # type: ignore[arg-type]
        files,
        audit,
        SIGNER,
        FixedClock(NOW),
        Metrics(),
        public_base_url="https://files.example.com",
        default_ttl=600,
        max_ttl=3600,
    )
    return handler, files, audit, session


def cmd(ttl: int | None = None, owner: str = "alice") -> CreateSignedLinkCommand:
    return CreateSignedLinkCommand(owner_id=owner, file_id="file-1", ttl_seconds=ttl, context=RequestContext("req_9"))


def test_link_is_verifiable_and_records_audit(parts) -> None:  # type: ignore[no-untyped-def]
    handler, files, audit, session = parts
    files.records["file-1"] = record()
    link = handler.execute(cmd(ttl=120))

    assert link.file_id == "file-1"
    assert link.ttl_seconds == 120
    assert link.expires_at == NOW + timedelta(seconds=120)
    assert link.key_id == "k1"
    parsed = urlparse(link.url)
    assert parsed.scheme == "https" and parsed.netloc == "files.example.com"
    assert parsed.path == "/v1/download/file-1"
    q = parse_qs(parsed.query)
    assert q["kid"] == ["k1"] and q["lid"] == [link.link_id]
    assert int(q["exp"][0]) == int(link.expires_at.timestamp())
    SIGNER.verify("file-1", link.link_id, int(q["exp"][0]), "k1", q["sig"][0])

    events = audit.of_type(AuditEventType.LINK_GENERATED)
    assert len(events) == 1
    event = events[0]
    assert event.actor_id == "alice" and event.link_id == link.link_id and event.request_id == "req_9"
    assert event.expires_at == link.expires_at
    assert event.metadata == {"ttl_seconds": 120, "key_id": "k1"}
    assert session.commits == 1


def test_default_ttl_applied(parts) -> None:  # type: ignore[no-untyped-def]
    handler, files, _, _ = parts
    files.records["file-1"] = record()
    assert handler.execute(cmd()).ttl_seconds == 600


@pytest.mark.parametrize("ttl", [0, -1, 3601])
def test_ttl_out_of_range(parts, ttl: int) -> None:  # type: ignore[no-untyped-def]
    handler, files, audit, _ = parts
    files.records["file-1"] = record()
    with pytest.raises(InvalidTTL):
        handler.execute(cmd(ttl=ttl))
    assert audit.events == []


def test_other_owner_sees_not_found(parts) -> None:  # type: ignore[no-untyped-def]
    handler, files, _, _ = parts
    files.records["file-1"] = record(owner="bob")
    with pytest.raises(FileNotFound):
        handler.execute(cmd())


def test_missing_file(parts) -> None:  # type: ignore[no-untyped-def]
    handler, _, _, _ = parts
    with pytest.raises(FileNotFound):
        handler.execute(cmd())


def test_deleted_file_is_unavailable(parts) -> None:  # type: ignore[no-untyped-def]
    handler, files, _, _ = parts
    files.records["file-1"] = record(status=FileStatus.DELETED)
    with pytest.raises(FileUnavailable):
        handler.execute(cmd())


def test_each_link_is_unique(parts) -> None:  # type: ignore[no-untyped-def]
    handler, files, _, _ = parts
    files.records["file-1"] = record()
    a, b = handler.execute(cmd()), handler.execute(cmd())
    assert a.link_id != b.link_id and a.url != b.url
