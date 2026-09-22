"""DownloadService: order of checks, failure modes, audit on success."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.application.clock import FixedClock
from app.application.context import RequestContext
from app.application.services.download_service import DownloadService, SignedRequest
from app.core.security import UrlSigner
from app.core.telemetry import Metrics
from app.domain.files.entities import AuditEventType, FileRecord, FileStatus
from app.domain.files.exceptions import (
    FileNotFound,
    FileUnavailable,
    LinkExpired,
    LinkSignatureInvalid,
    StorageInconsistent,
)
from tests.fixtures.fakes import FakeSession, InMemoryAuditRepository, InMemoryFileRepository, InMemoryStorage

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
EXP = int(NOW.timestamp()) + 60
SIGNER = UrlSigner({"k1": b"0123456789abcdef0123456789abcdef", "k0": b"old-key-0123456789abcdef01234567"}, "k1")
CTX = RequestContext(request_id="req_d", client_ip="203.0.113.7")


class Harness:
    def __init__(self) -> None:
        self.session = FakeSession()
        self.files = InMemoryFileRepository()
        self.audit = InMemoryAuditRepository()
        self.storage = InMemoryStorage()
        self.clock = FixedClock(NOW)
        self.service = DownloadService(
            self.session,  # type: ignore[arg-type]
            self.files,
            self.audit,
            self.storage,
            SIGNER,
            self.clock,
            Metrics(),
        )
        self.storage.objects["aa/bb"] = b"payload"
        self.files.records["file-1"] = FileRecord(
            id="file-1",
            owner_id="alice",
            original_filename="doc.pdf",
            content_type="application/pdf",
            size_bytes=7,
            sha256="f" * 64,
            storage_key="aa/bb",
            status=FileStatus.AVAILABLE,
            created_at=NOW,
            updated_at=NOW,
        )

    def request(
        self, file_id: str = "file-1", exp: int = EXP, key_id: str = "k1", sig: str | None = None
    ) -> SignedRequest:
        signature = sig or SIGNER.sign(file_id, "link-1", exp).value
        return SignedRequest(file_id=file_id, expires_at=exp, link_id="link-1", key_id=key_id, signature=signature)


def test_valid_link_resolves_and_audits() -> None:
    h = Harness()
    target = h.service.resolve(h.request(), CTX)
    assert target.path == "/fake/aa/bb"
    assert target.filename == "doc.pdf" and target.content_type == "application/pdf"
    assert target.size_bytes == 7 and target.sha256 == "f" * 64
    events = h.audit.of_type(AuditEventType.FILE_DOWNLOADED)
    assert len(events) == 1 and events[0].link_id == "link-1" and events[0].client_ip == "203.0.113.7"
    assert events[0].actor_id is None  # downloads are anonymous
    assert h.session.commits == 1


def test_bad_signature_never_touches_database() -> None:
    h = Harness()
    with pytest.raises(LinkSignatureInvalid):
        h.service.resolve(h.request(sig="A" * 43), CTX)
    assert h.files.get_calls == 0
    assert h.audit.events == []


def test_unknown_key_id_is_signature_error() -> None:
    h = Harness()
    with pytest.raises(LinkSignatureInvalid) as exc:
        h.service.resolve(h.request(key_id="nope"), CTX)
    assert exc.value.context["reason"] == "UnknownKeyId"


def test_link_signed_with_previous_key_still_works() -> None:
    h = Harness()
    old_sig = UrlSigner({"k0": b"old-key-0123456789abcdef01234567"}, "k0").sign("file-1", "link-1", EXP).value
    h.service.resolve(h.request(key_id="k0", sig=old_sig), CTX)


def test_expired_link() -> None:
    h = Harness()
    h.clock.advance(60)  # now == exp
    with pytest.raises(LinkExpired):
        h.service.resolve(h.request(), CTX)
    assert h.files.get_calls == 0


def test_tampered_expiry_is_signature_error_not_expiry() -> None:
    """Extending exp in the URL must fail the signature check, not be honoured."""
    h = Harness()
    genuine = SIGNER.sign("file-1", "link-1", EXP).value
    with pytest.raises(LinkSignatureInvalid):
        h.service.resolve(h.request(exp=EXP + 100_000, sig=genuine), CTX)


def test_missing_file_is_not_found() -> None:
    h = Harness()
    with pytest.raises(FileNotFound):
        h.service.resolve(h.request(file_id="ghost"), CTX)


def test_deleted_file_is_unavailable() -> None:
    h = Harness()
    h.files.records["file-1"].status = FileStatus.DELETED
    with pytest.raises(FileUnavailable):
        h.service.resolve(h.request(), CTX)
    assert h.audit.events == []


def test_missing_bytes_is_storage_inconsistent() -> None:
    h = Harness()
    del h.storage.objects["aa/bb"]
    with pytest.raises(StorageInconsistent):
        h.service.resolve(h.request(), CTX)
