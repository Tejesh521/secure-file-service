"""FastAPI dependencies wiring HTTP requests to application handlers."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.application.clock import SystemClock
from app.application.commands.create_signed_link import CreateSignedLinkHandler
from app.application.commands.delete_file import DeleteFileHandler
from app.application.commands.upload_file import UploadFileHandler
from app.application.context import RequestContext
from app.application.queries.get_file import GetFileQuery
from app.application.queries.list_audit_events import ListAuditEventsQuery
from app.application.queries.list_files import ListFilesQuery
from app.application.services.download_service import DownloadService
from app.core.config import Settings
from app.core.logging import user_id_ctx
from app.core.security import ApiKeyAuthenticator, UrlSigner
from app.core.telemetry import Metrics
from app.infrastructure.repositories.audit_repository import SqlAlchemyAuditRepository
from app.infrastructure.repositories.file_repository import SqlAlchemyFileRepository
from app.infrastructure.storage.local import LocalFileStorage

API_KEY_HEADER = "X-API-Key"
_clock = SystemClock()


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_metrics(request: Request) -> Metrics:
    metrics: Metrics = request.app.state.metrics
    return metrics


def get_signer(request: Request) -> UrlSigner:
    signer: UrlSigner = request.app.state.signer
    return signer


def get_storage(request: Request) -> LocalFileStorage:
    storage: LocalFileStorage = request.app.state.storage
    return storage


def get_db(request: Request) -> Iterator[Session]:
    session: Session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def get_request_context(request: Request) -> RequestContext:
    client = request.client.host if request.client else None
    return RequestContext(request_id=getattr(request.state, "request_id", None), client_ip=client)


def get_current_user(
    request: Request,
    api_key: Annotated[str | None, Header(alias=API_KEY_HEADER, description="Static API key")] = None,
) -> str:
    authenticator: ApiKeyAuthenticator = request.app.state.authenticator
    user_id = authenticator.authenticate(api_key)
    if user_id is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "UNAUTHORIZED", "message": "Missing or invalid API key."},
            headers={"WWW-Authenticate": "ApiKey"},
        )
    request.state.user_id = user_id
    user_id_ctx.set(user_id)
    return user_id


SettingsDep = Annotated[Settings, Depends(get_settings)]
DbDep = Annotated[Session, Depends(get_db)]
UserDep = Annotated[str, Depends(get_current_user)]
ContextDep = Annotated[RequestContext, Depends(get_request_context)]
MetricsDep = Annotated[Metrics, Depends(get_metrics)]
SignerDep = Annotated[UrlSigner, Depends(get_signer)]
StorageDep = Annotated[LocalFileStorage, Depends(get_storage)]


def get_upload_handler(db: DbDep, storage: StorageDep, metrics: MetricsDep, settings: SettingsDep) -> UploadFileHandler:
    return UploadFileHandler(
        db,
        SqlAlchemyFileRepository(db),
        SqlAlchemyAuditRepository(db),
        storage,
        _clock,
        metrics,
        max_upload_bytes=settings.max_upload_bytes,
    )


def get_link_handler(
    db: DbDep, signer: SignerDep, metrics: MetricsDep, settings: SettingsDep
) -> CreateSignedLinkHandler:
    return CreateSignedLinkHandler(
        db,
        SqlAlchemyFileRepository(db),
        SqlAlchemyAuditRepository(db),
        signer,
        _clock,
        metrics,
        public_base_url=settings.public_base_url,
        default_ttl=settings.default_link_ttl_seconds,
        max_ttl=settings.max_link_ttl_seconds,
    )


def get_delete_handler(db: DbDep, storage: StorageDep, metrics: MetricsDep) -> DeleteFileHandler:
    return DeleteFileHandler(db, SqlAlchemyFileRepository(db), SqlAlchemyAuditRepository(db), storage, _clock, metrics)


def get_file_query(db: DbDep) -> GetFileQuery:
    return GetFileQuery(SqlAlchemyFileRepository(db))


def get_list_query(db: DbDep) -> ListFilesQuery:
    return ListFilesQuery(SqlAlchemyFileRepository(db))


def get_audit_query(db: DbDep) -> ListAuditEventsQuery:
    return ListAuditEventsQuery(SqlAlchemyFileRepository(db), SqlAlchemyAuditRepository(db))


def get_download_service(db: DbDep, storage: StorageDep, signer: SignerDep, metrics: MetricsDep) -> DownloadService:
    return DownloadService(
        db, SqlAlchemyFileRepository(db), SqlAlchemyAuditRepository(db), storage, signer, _clock, metrics
    )
