"""Application configuration loaded from the environment.

All tunables live here so that behaviour is explicit and discoverable. Values are
validated at startup; a misconfigured production deployment fails fast instead of
serving requests with weak secrets.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIB = 1024 * 1024

# Development-only defaults. Production configuration rejects these values.
_DEV_SIGNING_KEYS = "dev:dev-signing-secret-do-not-use-in-production-0123456789"
_DEV_API_KEYS = "dev-key-alice:alice,dev-key-bob:bob"


def _parse_pairs(raw: str, *, name: str) -> dict[str, str]:
    """Parse ``"a:b,c:d"`` into ``{"a": "b", "c": "d"}``."""
    result: dict[str, str] = {}
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise ValueError(f"{name} entries must look like 'id:value', got {chunk!r}")
        key, value = chunk.split(":", 1)
        key, value = key.strip(), value.strip()
        if not key or not value:
            raise ValueError(f"{name} entries must have a non-empty id and value")
        if key in result:
            raise ValueError(f"{name} contains duplicate id {key!r}")
        result[key] = value
    if not result:
        raise ValueError(f"{name} must contain at least one entry")
    return result


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Service identity
    app_name: str = "secure-file-service"
    environment: Literal["development", "test", "staging", "production"] = "development"
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    # HTTP
    host: str = "0.0.0.0"  # noqa: S104 - container-friendly bind address
    port: int = Field(default=8080, ge=1, le=65535)
    public_base_url: str = "http://localhost:8080"
    trusted_hosts: str = "*"
    cors_origins: str = ""
    max_request_body_bytes: int = Field(default=1 * MIB, ge=1024)
    shutdown_timeout_seconds: int = Field(default=20, ge=1)

    # Database
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/files"
    database_pool_size: int = Field(default=10, ge=1)
    database_max_overflow: int = Field(default=10, ge=0)
    database_connect_timeout_seconds: int = Field(default=5, ge=1)
    database_statement_timeout_ms: int = Field(default=5000, ge=100)

    # Storage (private, never served directly)
    storage_root: Path = Path("./var/storage")
    max_upload_bytes: int = Field(default=100 * MIB, ge=1)
    upload_chunk_bytes: int = Field(default=64 * 1024, ge=1024)

    # Signed links
    signing_keys: str = _DEV_SIGNING_KEYS
    signing_active_key_id: str | None = None
    default_link_ttl_seconds: int = Field(default=3600, ge=1)
    max_link_ttl_seconds: int = Field(default=7 * 24 * 3600, ge=1)

    # Authentication (static API keys -> user id)
    api_keys: str = _DEV_API_KEYS

    # Observability
    metrics_enabled: bool = True
    otel_enabled: bool = False
    otel_service_name: str = "secure-file-service"
    otel_exporter_otlp_endpoint: str | None = None

    @field_validator("public_base_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("log_level")
    @classmethod
    def _upper_level(cls, value: str) -> str:
        level = value.upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"unsupported log level {value!r}")
        return level

    @property
    def signing_key_ring(self) -> dict[str, bytes]:
        return {k: v.encode("utf-8") for k, v in _parse_pairs(self.signing_keys, name="SIGNING_KEYS").items()}

    @property
    def active_signing_key_id(self) -> str:
        ring = self.signing_key_ring
        if self.signing_active_key_id is None:
            return next(iter(ring))
        return self.signing_active_key_id

    @property
    def api_key_map(self) -> dict[str, str]:
        return _parse_pairs(self.api_keys, name="API_KEYS")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def trusted_host_list(self) -> list[str]:
        return [h.strip() for h in self.trusted_hosts.split(",") if h.strip()] or ["*"]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @model_validator(mode="after")
    def _validate(self) -> Settings:
        ring = self.signing_key_ring
        if self.signing_active_key_id is not None and self.signing_active_key_id not in ring:
            raise ValueError("SIGNING_ACTIVE_KEY_ID must reference an id present in SIGNING_KEYS")
        _ = self.api_key_map
        if self.default_link_ttl_seconds > self.max_link_ttl_seconds:
            raise ValueError("DEFAULT_LINK_TTL_SECONDS cannot exceed MAX_LINK_TTL_SECONDS")
        if self.is_production:
            if self.signing_keys == _DEV_SIGNING_KEYS:
                raise ValueError("SIGNING_KEYS must be set explicitly in production")
            if self.api_keys == _DEV_API_KEYS:
                raise ValueError("API_KEYS must be set explicitly in production")
            for kid, secret in ring.items():
                if len(secret) < 32:
                    raise ValueError(f"signing key {kid!r} must be at least 32 bytes in production")
            if not self.public_base_url.startswith("https://"):
                raise ValueError("PUBLIC_BASE_URL must use https in production")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
