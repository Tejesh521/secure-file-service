"""Domain exceptions. The API layer maps these to HTTP status codes and error codes."""

from __future__ import annotations


class DomainError(Exception):
    code = "DOMAIN_ERROR"
    message = "Request could not be processed."

    def __init__(self, message: str | None = None, **context: object) -> None:
        super().__init__(message or self.message)
        self.message = message or self.message
        self.context = context


class FileNotFound(DomainError):
    code = "FILE_NOT_FOUND"
    message = "File not found."


class FileUnavailable(DomainError):
    code = "FILE_UNAVAILABLE"
    message = "File is no longer available."


class EmptyUpload(DomainError):
    code = "EMPTY_UPLOAD"
    message = "Uploaded file is empty."


class UploadTooLarge(DomainError):
    code = "PAYLOAD_TOO_LARGE"
    message = "Uploaded file exceeds the maximum allowed size."


class InvalidTTL(DomainError):
    code = "INVALID_TTL"
    message = "Requested TTL is outside the allowed range."


class LinkSignatureInvalid(DomainError):
    code = "LINK_SIGNATURE_INVALID"
    message = "Signed link is invalid."


class LinkExpired(DomainError):
    code = "LINK_EXPIRED"
    message = "Signed link has expired."


class IdempotencyConflict(DomainError):
    code = "IDEMPOTENCY_KEY_REUSED"
    message = "Idempotency-Key was already used with a different payload."


class StorageInconsistent(DomainError):
    code = "STORAGE_INCONSISTENT"
    message = "File metadata exists but the stored object is missing."


class InvalidStateTransition(DomainError):
    code = "INVALID_STATE_TRANSITION"
    message = "File cannot move to the requested state."
