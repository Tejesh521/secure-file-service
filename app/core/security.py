"""Cryptographic primitives: URL signing and API key verification.

The signer is deliberately stateless. A signed link encodes everything needed to
verify it (file id, link id, expiry, key id) and the HMAC over those fields.
Because the secret lives in configuration rather than memory, links survive
restarts and any replica can verify a link produced by another.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from collections.abc import Mapping
from dataclasses import dataclass

SIGNATURE_VERSION = "v1"
_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


class SignatureError(Exception):
    """Base class for signature failures."""


class UnknownKeyId(SignatureError):
    pass


class SignatureMismatch(SignatureError):
    pass


@dataclass(frozen=True, slots=True)
class Signature:
    key_id: str
    value: str  # URL-safe base64 without padding


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class UrlSigner:
    """HMAC-SHA256 signer with a key ring for rotation.

    New links are signed with the active key; verification accepts any key in the
    ring so that old links stay valid while a rotation is in progress.
    """

    def __init__(self, key_ring: Mapping[str, bytes], active_key_id: str) -> None:
        if not key_ring:
            raise ValueError("key ring must not be empty")
        if active_key_id not in key_ring:
            raise ValueError("active key id must be present in key ring")
        self._keys = dict(key_ring)
        self._active = active_key_id

    @property
    def active_key_id(self) -> str:
        return self._active

    @staticmethod
    def message(file_id: str, link_id: str, expires_at: int) -> bytes:
        # Newline-delimited to make the encoding unambiguous.
        return f"{SIGNATURE_VERSION}\n{file_id}\n{link_id}\n{expires_at}".encode()

    def sign(self, file_id: str, link_id: str, expires_at: int) -> Signature:
        digest = hmac.new(self._keys[self._active], self.message(file_id, link_id, expires_at), hashlib.sha256).digest()
        return Signature(key_id=self._active, value=_b64url(digest))

    def verify(self, file_id: str, link_id: str, expires_at: int, key_id: str, signature: str) -> None:
        """Raise ``SignatureError`` unless ``signature`` is valid for the inputs.

        Expiry is *not* checked here; the caller decides how to treat time so that
        the signer stays pure and easily testable.
        """
        key = self._keys.get(key_id)
        if key is None:
            raise UnknownKeyId(key_id)
        expected = _b64url(hmac.new(key, self.message(file_id, link_id, expires_at), hashlib.sha256).digest())
        if not hmac.compare_digest(expected.encode("ascii"), signature.encode("utf-8", "replace")):
            raise SignatureMismatch()


class ApiKeyAuthenticator:
    """Maps static API keys to user ids using constant-time comparison."""

    def __init__(self, key_to_user: Mapping[str, str]) -> None:
        self._entries = [(k.encode("utf-8"), u) for k, u in key_to_user.items()]

    def authenticate(self, presented: str | None) -> str | None:
        if not presented:
            return None
        candidate = presented.encode("utf-8", "replace")
        matched: str | None = None
        # Compare against every key so timing does not reveal which key exists.
        for key, user_id in self._entries:
            if hmac.compare_digest(key, candidate):
                matched = user_id
        return matched


def new_secret(length: int = 48) -> str:
    """Generate a random secret suitable for SIGNING_KEYS."""
    return secrets.token_urlsafe(length)


def new_id(prefix: str = "", length: int = 26) -> str:
    """Generate a random, URL-safe lowercase identifier."""
    body = "".join(secrets.choice(_ID_ALPHABET) for _ in range(length))
    return f"{prefix}{body}" if prefix else body
