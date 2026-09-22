"""Pure domain logic: filename hygiene, TTL rules and link expiry math."""

from __future__ import annotations

import posixpath
import re
import unicodedata
import urllib.parse
from datetime import datetime, timedelta

from app.domain.files.exceptions import InvalidTTL

MAX_FILENAME_LENGTH = 255
DEFAULT_FILENAME = "upload.bin"
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_CONTENT_TYPE = re.compile(r"^[a-zA-Z0-9!#$&^_.+-]{1,64}/[a-zA-Z0-9!#$&^_.+-]{1,127}(;.*)?$")
DEFAULT_CONTENT_TYPE = "application/octet-stream"


def sanitize_filename(raw: str | None) -> str:
    """Reduce a client-supplied filename to a safe display name.

    The result is *only* used for metadata and the Content-Disposition header; it
    is never used to build a filesystem path, so this is defence in depth rather
    than the sole control against path traversal.
    """
    if not raw:
        return DEFAULT_FILENAME
    name = unicodedata.normalize("NFC", raw)
    name = name.replace("\\", "/")
    name = posixpath.basename(name)
    name = _CONTROL_CHARS.sub("", name).strip().strip(".")
    if not name or name in {".", ".."}:
        return DEFAULT_FILENAME
    if len(name) > MAX_FILENAME_LENGTH:
        stem, dot, ext = name.rpartition(".")
        if dot and len(ext) <= 16:
            name = stem[: MAX_FILENAME_LENGTH - len(ext) - 1] + "." + ext
        else:
            name = name[:MAX_FILENAME_LENGTH]
    return name


def normalize_content_type(raw: str | None) -> str:
    if not raw:
        return DEFAULT_CONTENT_TYPE
    value = raw.strip()
    if len(value) > 255 or not _CONTENT_TYPE.match(value):
        return DEFAULT_CONTENT_TYPE
    return value


def content_disposition(filename: str) -> str:
    """Build an RFC 6266 header with an ASCII fallback and a UTF-8 ``filename*``."""
    ascii_fallback = filename.encode("ascii", "replace").decode("ascii").replace('"', "'").replace("\\", "_")
    encoded = urllib.parse.quote(filename, safe="")
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"


def validate_ttl(ttl_seconds: int | None, *, default: int, maximum: int) -> int:
    ttl = default if ttl_seconds is None else ttl_seconds
    if ttl < 1 or ttl > maximum:
        raise InvalidTTL(f"ttl_seconds must be between 1 and {maximum}", ttl=ttl, maximum=maximum)
    return ttl


def expiry_for(now: datetime, ttl_seconds: int) -> datetime:
    return now + timedelta(seconds=ttl_seconds)


def is_expired(expires_at_epoch: int, now: datetime) -> bool:
    return int(now.timestamp()) >= expires_at_epoch
