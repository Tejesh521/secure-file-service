"""Issue a signed, time-limited download URL and record the audit event."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from app.application.context import RequestContext
from app.core.security import UrlSigner
from app.core.telemetry import Metrics
from app.domain.files.entities import AuditEvent, AuditEventType, SignedLink
from app.domain.files.exceptions import FileNotFound, FileUnavailable
from app.domain.files.ports import AuditRepository, Clock, FileRepository
from app.domain.files.services import expiry_for, validate_ttl

logger = logging.getLogger(__name__)

DOWNLOAD_PATH_TEMPLATE = "/v1/download/{file_id}"


def build_download_url(base_url: str, file_id: str, expires_at: int, link_id: str, key_id: str, sig: str) -> str:
    query = urlencode({"exp": expires_at, "lid": link_id, "kid": key_id, "sig": sig})
    return f"{base_url}{DOWNLOAD_PATH_TEMPLATE.format(file_id=file_id)}?{query}"


@dataclass(frozen=True, slots=True)
class CreateSignedLinkCommand:
    owner_id: str
    file_id: str
    ttl_seconds: int | None
    context: RequestContext


class CreateSignedLinkHandler:
    def __init__(
        self,
        session: Session,
        files: FileRepository,
        audit: AuditRepository,
        signer: UrlSigner,
        clock: Clock,
        metrics: Metrics,
        *,
        public_base_url: str,
        default_ttl: int,
        max_ttl: int,
    ) -> None:
        self._session = session
        self._files = files
        self._audit = audit
        self._signer = signer
        self._clock = clock
        self._metrics = metrics
        self._base_url = public_base_url
        self._default_ttl = default_ttl
        self._max_ttl = max_ttl

    def execute(self, cmd: CreateSignedLinkCommand) -> SignedLink:
        ttl = validate_ttl(cmd.ttl_seconds, default=self._default_ttl, maximum=self._max_ttl)
        record = self._files.get_for_owner(cmd.file_id, cmd.owner_id)
        if record is None:
            raise FileNotFound(file_id=cmd.file_id)
        if not record.is_available:
            raise FileUnavailable(file_id=cmd.file_id)

        now = self._clock.now()
        expires_at = expiry_for(now, ttl)
        expires_epoch = int(expires_at.timestamp())
        link_id = str(uuid.uuid4())
        signature = self._signer.sign(record.id, link_id, expires_epoch)
        url = build_download_url(self._base_url, record.id, expires_epoch, link_id, signature.key_id, signature.value)

        self._audit.add(
            AuditEvent(
                id=str(uuid.uuid4()),
                file_id=record.id,
                event_type=AuditEventType.LINK_GENERATED,
                actor_id=cmd.owner_id,
                link_id=link_id,
                expires_at=expires_at,
                request_id=cmd.context.request_id,
                client_ip=cmd.context.client_ip,
                metadata={"ttl_seconds": ttl, "key_id": signature.key_id},
                created_at=now,
            )
        )
        self._session.commit()
        self._metrics.links_generated_total.inc()
        logger.info(
            "signed link generated",
            extra={"file_id": record.id, "link_id": link_id, "ttl_seconds": ttl, "key_id": signature.key_id},
        )
        return SignedLink(
            link_id=link_id, file_id=record.id, url=url, expires_at=expires_at, key_id=signature.key_id, ttl_seconds=ttl
        )
