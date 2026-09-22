"""URL signer and API-key authenticator."""

from __future__ import annotations

import pytest

from app.core.security import (
    ApiKeyAuthenticator,
    SignatureMismatch,
    UnknownKeyId,
    UrlSigner,
    new_id,
    new_secret,
)

RING = {"k1": b"secret-one-0123456789abcdef", "k0": b"secret-zero-0123456789abcdef"}


@pytest.fixture
def signer() -> UrlSigner:
    return UrlSigner(RING, "k1")


def test_sign_verify_roundtrip(signer: UrlSigner) -> None:
    sig = signer.sign("file-1", "link-1", 1_800_000_000)
    assert sig.key_id == "k1"
    assert len(sig.value) == 43  # 32-byte HMAC, URL-safe base64 without padding
    signer.verify("file-1", "link-1", 1_800_000_000, sig.key_id, sig.value)


def test_signature_is_deterministic(signer: UrlSigner) -> None:
    assert signer.sign("f", "l", 1).value == signer.sign("f", "l", 1).value


@pytest.mark.parametrize(
    ("file_id", "link_id", "exp"),
    [("file-2", "link-1", 1_800_000_000), ("file-1", "link-2", 1_800_000_000), ("file-1", "link-1", 1_800_000_001)],
)
def test_any_field_change_invalidates_signature(signer: UrlSigner, file_id: str, link_id: str, exp: int) -> None:
    sig = signer.sign("file-1", "link-1", 1_800_000_000)
    with pytest.raises(SignatureMismatch):
        signer.verify(file_id, link_id, exp, sig.key_id, sig.value)


def test_tampered_signature_rejected(signer: UrlSigner) -> None:
    sig = signer.sign("file-1", "link-1", 1)
    flipped = ("A" if sig.value[0] != "A" else "B") + sig.value[1:]
    with pytest.raises(SignatureMismatch):
        signer.verify("file-1", "link-1", 1, sig.key_id, flipped)


def test_garbage_signature_rejected_without_error(signer: UrlSigner) -> None:
    with pytest.raises(SignatureMismatch):
        signer.verify("file-1", "link-1", 1, "k1", "not base64 at all é")


def test_unknown_key_id_rejected(signer: UrlSigner) -> None:
    sig = signer.sign("file-1", "link-1", 1)
    with pytest.raises(UnknownKeyId):
        signer.verify("file-1", "link-1", 1, "k9", sig.value)


def test_key_rotation_keeps_old_links_valid() -> None:
    old = UrlSigner({"k0": RING["k0"]}, "k0")
    sig = old.sign("file-1", "link-1", 1)
    rotated = UrlSigner(RING, "k1")
    rotated.verify("file-1", "link-1", 1, "k0", sig.value)  # still accepted
    assert rotated.sign("file-1", "link-1", 1).key_id == "k1"  # new links use the new key


def test_signer_is_stateless_across_instances() -> None:
    """Simulates a restart: a fresh signer with the same config verifies old links."""
    sig = UrlSigner(RING, "k1").sign("file-1", "link-1", 1)
    UrlSigner(dict(RING), "k1").verify("file-1", "link-1", 1, "k1", sig.value)


def test_signer_rejects_bad_configuration() -> None:
    with pytest.raises(ValueError, match="empty"):
        UrlSigner({}, "k1")
    with pytest.raises(ValueError, match="active key"):
        UrlSigner(RING, "missing")


def test_message_encoding_is_unambiguous() -> None:
    assert UrlSigner.message("a", "b", 1) != UrlSigner.message("ab", "", 1)


def test_api_key_authenticator() -> None:
    auth = ApiKeyAuthenticator({"key-a": "alice", "key-b": "bob"})
    assert auth.authenticate("key-a") == "alice"
    assert auth.authenticate("key-b") == "bob"
    assert auth.authenticate("key-c") is None
    assert auth.authenticate("") is None
    assert auth.authenticate(None) is None
    assert auth.authenticate("key-a ") is None


def test_new_secret_and_id_are_random_and_well_formed() -> None:
    assert new_secret() != new_secret()
    assert len(new_secret(48)) >= 48
    ident = new_id("f_", 10)
    assert ident.startswith("f_") and len(ident) == 12
    assert ident.lower() == ident
