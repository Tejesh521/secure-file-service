"""Settings parsing and production safety rails."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings, _parse_pairs


def settings(**kwargs: Any) -> Settings:
    return Settings(_env_file=None, **kwargs)  # type: ignore[call-arg]


def test_parse_pairs() -> None:
    assert _parse_pairs("a:1, b:2 ,", name="X") == {"a": "1", "b": "2"}
    assert _parse_pairs("k:with:colons", name="X") == {"k": "with:colons"}
    for bad in ["", "a", "a:", ":b", "a:1,a:2"]:
        with pytest.raises(ValueError, match="X"):
            _parse_pairs(bad, name="X")


def test_development_defaults_load() -> None:
    s = settings()
    assert s.environment == "development"
    assert s.active_signing_key_id == "dev"
    assert s.api_key_map["dev-key-alice"] == "alice"
    assert s.trusted_host_list == ["*"]
    assert s.cors_origin_list == []


def test_derived_lists_and_url_normalisation() -> None:
    s = settings(
        public_base_url="https://files.example.com/",
        cors_origins="https://a.com, https://b.com",
        trusted_hosts="a.com,b.com",
    )
    assert s.public_base_url == "https://files.example.com"
    assert s.cors_origin_list == ["https://a.com", "https://b.com"]
    assert s.trusted_host_list == ["a.com", "b.com"]


def test_active_key_must_exist_in_ring() -> None:
    with pytest.raises(ValueError, match="SIGNING_ACTIVE_KEY_ID"):
        settings(signing_keys="k1:secret-0123456789abcdef0123456789", signing_active_key_id="k2")


def test_explicit_active_key_is_used() -> None:
    s = settings(
        signing_keys="k1:secret-0123456789abcdef0123456789,k2:another-0123456789abcdef0123456789",
        signing_active_key_id="k2",
    )
    assert s.active_signing_key_id == "k2"
    assert set(s.signing_key_ring) == {"k1", "k2"}


def test_default_ttl_cannot_exceed_max() -> None:
    with pytest.raises(ValueError, match="DEFAULT_LINK_TTL_SECONDS"):
        settings(default_link_ttl_seconds=100, max_link_ttl_seconds=10)


def test_log_level_validation() -> None:
    assert settings(log_level="debug").log_level == "DEBUG"
    with pytest.raises(ValueError, match="log level"):
        settings(log_level="loud")


PROD = {
    "environment": "production",
    "signing_keys": "p1:" + "x" * 48,
    "api_keys": "prod-key:svc",
    "public_base_url": "https://files.example.com",
}


def test_production_accepts_explicit_secure_config(tmp_path: Path) -> None:
    s = settings(**PROD, storage_root=tmp_path)
    assert s.is_production


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"signing_keys": "dev:dev-signing-secret-do-not-use-in-production-0123456789"}, "SIGNING_KEYS"),
        ({"api_keys": "dev-key-alice:alice,dev-key-bob:bob"}, "API_KEYS"),
        ({"signing_keys": "p1:short"}, "at least 32 bytes"),
        ({"public_base_url": "http://files.example.com"}, "https"),
    ],
)
def test_production_rejects_weak_configuration(override: dict[str, str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        settings(**{**PROD, **override})


def test_bounds_are_enforced() -> None:
    with pytest.raises(ValueError):
        settings(port=0)
    with pytest.raises(ValueError):
        settings(max_upload_bytes=0)
    with pytest.raises(ValueError):
        settings(environment="prod")
