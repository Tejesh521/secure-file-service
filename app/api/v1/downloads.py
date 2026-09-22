"""Public download endpoint. Authenticated by the signature alone."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse as StarletteFileResponse

from app.api.dependencies import ContextDep, get_download_service
from app.application.services.download_service import DownloadService, SignedRequest
from app.domain.files.services import content_disposition
from app.schemas.common import ErrorResponse

router = APIRouter(prefix="/download", tags=["download"])

_SIG_PATTERN = r"^[A-Za-z0-9_\-]{43}$"


@router.get(
    "/{file_id}",
    summary="Download a file via a signed link",
    response_class=StarletteFileResponse,
    responses={
        200: {"content": {"application/octet-stream": {}}, "description": "File bytes"},
        403: {"model": ErrorResponse, "description": "Signature invalid"},
        404: {"model": ErrorResponse, "description": "File not found"},
        410: {"model": ErrorResponse, "description": "Link expired or file deleted"},
        422: {"model": ErrorResponse, "description": "Malformed query parameters"},
    },
)
def download_file(
    file_id: str,
    context: ContextDep,
    service: Annotated[DownloadService, Depends(get_download_service)],
    exp: Annotated[int, Query(ge=0, description="Expiry as Unix epoch seconds")],
    lid: Annotated[str, Query(min_length=1, max_length=64, description="Link id")],
    kid: Annotated[str, Query(min_length=1, max_length=64, description="Signing key id")],
    sig: Annotated[str, Query(pattern=_SIG_PATTERN, description="URL-safe base64 HMAC-SHA256")],
) -> StarletteFileResponse:
    target = service.resolve(
        SignedRequest(file_id=file_id, expires_at=exp, link_id=lid, key_id=kid, signature=sig), context
    )
    headers = {
        "Content-Disposition": content_disposition(target.filename),
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "ETag": f'"{target.sha256}"',
    }
    return StarletteFileResponse(target.path, media_type=target.content_type, headers=headers)
