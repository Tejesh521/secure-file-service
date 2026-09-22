from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Facts about the calling request that the audit trail records."""

    request_id: str | None = None
    client_ip: str | None = None
