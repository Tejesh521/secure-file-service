"""Owner-facing file endpoints. All routes require an API key."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Header, Query, Response, UploadFile, status

from app.api.dependencies import (
    ContextDep,
    UserDep,
    get_audit_query,
    get_delete_handler,
    get_file_query,
    get_link_handler,
    get_list_query,
    get_upload_handler,
)
from app.application.commands.create_signed_link import CreateSignedLinkCommand, CreateSignedLinkHandler
from app.application.commands.delete_file import DeleteFileCommand, DeleteFileHandler
from app.application.commands.upload_file import UploadFileCommand, UploadFileHandler
from app.application.queries.get_file import GetFileQuery
from app.application.queries.list_audit_events import ListAuditEventsQuery
from app.application.queries.list_files import ListFilesQuery
from app.schemas.common import ErrorResponse, PageMeta
from app.schemas.files import (
    AuditEventListResponse,
    AuditEventResponse,
    CreateLinkRequest,
    FileListResponse,
    FileResponse,
    SignedLinkResponse,
)

router = APIRouter(prefix="/files", tags=["files"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing or invalid API key"},
    404: {"model": ErrorResponse, "description": "File not found (or not owned by caller)"},
    422: {"model": ErrorResponse, "description": "Validation error"},
}

LimitQuery = Annotated[int, Query(ge=1, le=100, description="Page size")]
OffsetQuery = Annotated[int, Query(ge=0, le=1_000_000, description="Items to skip")]
IdempotencyKeyHeader = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z0-9_.:\-]+$",
        description="Optional client-chosen key making the upload safe to retry.",
    ),
]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=FileResponse,
    summary="Upload a private file",
    responses={
        **_ERRORS,
        200: {"model": FileResponse, "description": "Replay of an earlier upload with the same Idempotency-Key"},
        409: {"model": ErrorResponse, "description": "Idempotency-Key reused with different content"},
        413: {"model": ErrorResponse, "description": "File exceeds MAX_UPLOAD_BYTES"},
    },
)
def upload_file(
    response: Response,
    user_id: UserDep,
    context: ContextDep,
    handler: Annotated[UploadFileHandler, Depends(get_upload_handler)],
    file: Annotated[UploadFile, File(description="Multipart field named 'file'")],
    idempotency_key: IdempotencyKeyHeader = None,
) -> FileResponse:
    result = handler.execute(
        UploadFileCommand(
            owner_id=user_id,
            filename=file.filename,
            content_type=file.content_type,
            stream=file.file,
            idempotency_key=idempotency_key,
            context=context,
        )
    )
    if result.replayed:
        response.status_code = status.HTTP_200_OK
    response.headers["Location"] = f"/v1/files/{result.file.id}"
    return FileResponse.from_entity(result.file)


@router.get("", response_model=FileListResponse, summary="List my files", responses={401: _ERRORS[401]})
def list_files(
    user_id: UserDep,
    query: Annotated[ListFilesQuery, Depends(get_list_query)],
    limit: LimitQuery = 20,
    offset: OffsetQuery = 0,
) -> FileListResponse:
    items, total = query.execute(user_id, limit=limit, offset=offset)
    return FileListResponse(
        items=[FileResponse.from_entity(r) for r in items], page=PageMeta(limit=limit, offset=offset, total=total)
    )


@router.get("/{file_id}", response_model=FileResponse, summary="Get file metadata", responses=_ERRORS)
def get_file(file_id: str, user_id: UserDep, query: Annotated[GetFileQuery, Depends(get_file_query)]) -> FileResponse:
    return FileResponse.from_entity(query.execute(user_id, file_id))


@router.delete(
    "/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a file (invalidates all signed links)",
    responses={401: _ERRORS[401], 404: _ERRORS[404]},
)
def delete_file(
    file_id: str,
    user_id: UserDep,
    context: ContextDep,
    handler: Annotated[DeleteFileHandler, Depends(get_delete_handler)],
) -> Response:
    handler.execute(DeleteFileCommand(owner_id=user_id, file_id=file_id, context=context))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{file_id}/links",
    status_code=status.HTTP_201_CREATED,
    response_model=SignedLinkResponse,
    summary="Generate a signed download link",
    responses={**_ERRORS, 410: {"model": ErrorResponse, "description": "File has been deleted"}},
)
def create_link(
    file_id: str,
    user_id: UserDep,
    context: ContextDep,
    handler: Annotated[CreateSignedLinkHandler, Depends(get_link_handler)],
    body: CreateLinkRequest | None = None,
) -> SignedLinkResponse:
    ttl = body.ttl_seconds if body else None
    link = handler.execute(CreateSignedLinkCommand(owner_id=user_id, file_id=file_id, ttl_seconds=ttl, context=context))
    return SignedLinkResponse.from_entity(link)


@router.get(
    "/{file_id}/audit",
    response_model=AuditEventListResponse,
    summary="List audit events for a file",
    responses=_ERRORS,
)
def list_audit_events(
    file_id: str,
    user_id: UserDep,
    query: Annotated[ListAuditEventsQuery, Depends(get_audit_query)],
    limit: LimitQuery = 20,
    offset: OffsetQuery = 0,
) -> AuditEventListResponse:
    items, total = query.execute(user_id, file_id, limit=limit, offset=offset)
    return AuditEventListResponse(
        items=[AuditEventResponse.from_entity(e) for e in items],
        page=PageMeta(limit=limit, offset=offset, total=total),
    )
