from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorDetail(BaseModel):
    field: str | None = None
    reason: str


class ErrorBody(BaseModel):
    code: str = Field(examples=["VALIDATION_ERROR"])
    message: str
    details: list[ErrorDetail] = Field(default_factory=list)
    request_id: str | None = None


class ErrorResponse(BaseModel):
    """Single error envelope used by every non-2xx response."""

    error: ErrorBody


class PageMeta(BaseModel):
    limit: int
    offset: int
    total: int


class HealthResponse(BaseModel):
    status: str
    checks: dict[str, Any] = Field(default_factory=dict)


class MessageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str
