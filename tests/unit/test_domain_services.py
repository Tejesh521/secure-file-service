"""Pure domain helpers: filename hygiene, content types, TTL and expiry rules."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.files.exceptions import InvalidTTL
from app.domain.files.services import (
    DEFAULT_CONTENT_TYPE,
    DEFAULT_FILENAME,
    MAX_FILENAME_LENGTH,
    content_disposition,
    expiry_for,
    is_expired,
    normalize_content_type,
    sanitize_filename,
    validate_ttl,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("report.pdf", "report.pdf"),
        ("../../etc/passwd", "passwd"),
        ("..\\..\\windows\\system.ini", "system.ini"),
        ("/absolute/path/photo.jpg", "photo.jpg"),
        ("name\x00with\x1fcontrol.txt", "namewithcontrol.txt"),
        ("  spaced .txt  ", "spaced .txt"),
        ("...", DEFAULT_FILENAME),
        ("..", DEFAULT_FILENAME),
        ("", DEFAULT_FILENAME),
        (None, DEFAULT_FILENAME),
        ("résumé.pdf", "résumé.pdf"),
    ],
)
def test_sanitize_filename(raw: str | None, expected: str) -> None:
    assert sanitize_filename(raw) == expected


def test_sanitize_filename_truncates_but_keeps_extension() -> None:
    name = sanitize_filename("a" * 300 + ".txt")
    assert len(name) == MAX_FILENAME_LENGTH
    assert name.endswith(".txt")


def test_sanitize_filename_truncates_without_extension() -> None:
    assert len(sanitize_filename("b" * 400)) == MAX_FILENAME_LENGTH


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("text/plain", "text/plain"),
        ("application/json; charset=utf-8", "application/json; charset=utf-8"),
        ("image/svg+xml", "image/svg+xml"),
        ("  text/csv ", "text/csv"),
        ("", DEFAULT_CONTENT_TYPE),
        (None, DEFAULT_CONTENT_TYPE),
        ("not-a-type", DEFAULT_CONTENT_TYPE),
        ("text/plain\r\nX-Injected: 1", DEFAULT_CONTENT_TYPE),
        ("a/" + "b" * 300, DEFAULT_CONTENT_TYPE),
    ],
)
def test_normalize_content_type(raw: str | None, expected: str) -> None:
    assert normalize_content_type(raw) == expected


def test_content_disposition_is_header_safe() -> None:
    header = content_disposition('we"ird \\ résumé.pdf')
    assert header.startswith("attachment; ")
    assert '"' not in header.split("filename=")[1].split(";")[0].strip('"')
    assert "filename*=UTF-8''" in header
    assert "\n" not in header and "\r" not in header


def test_validate_ttl_defaults_and_bounds() -> None:
    assert validate_ttl(None, default=600, maximum=3600) == 600
    assert validate_ttl(1, default=600, maximum=3600) == 1
    assert validate_ttl(3600, default=600, maximum=3600) == 3600
    with pytest.raises(InvalidTTL):
        validate_ttl(0, default=600, maximum=3600)
    with pytest.raises(InvalidTTL):
        validate_ttl(3601, default=600, maximum=3600)
    with pytest.raises(InvalidTTL):
        validate_ttl(-5, default=600, maximum=3600)


def test_expiry_and_is_expired() -> None:
    now = datetime(2026, 9, 22, 12, 0, 0, tzinfo=UTC)
    expires = expiry_for(now, 60)
    assert expires == datetime(2026, 9, 22, 12, 1, 0, tzinfo=UTC)
    epoch = int(expires.timestamp())
    assert not is_expired(epoch, now)
    assert not is_expired(epoch, datetime(2026, 9, 22, 12, 0, 59, tzinfo=UTC))
    assert is_expired(epoch, expires)  # boundary: expiry instant is expired
    assert is_expired(epoch, datetime(2026, 9, 22, 12, 5, 0, tzinfo=UTC))
