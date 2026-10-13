"""Structured, safe, traceable errors.

Every error that can reach a client is a ``ForgeError``. The payload shape matches the
platform specification::

    {"error": {"code": "...", "message": "...", "request_id": "..."}}

Messages must never contain secrets, stack traces, SQL or internal paths.
"""

from __future__ import annotations

from typing import Any, Mapping


class ErrorCode:
    INVALID_ARGUMENT = "invalid_argument"
    INVALID_REQUEST = "invalid_request"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    INSUFFICIENT_SCOPE = "insufficient_scope"
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    CONFLICT = "conflict"
    RATE_LIMITED = "rate_limited"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    REQUEST_TIMEOUT = "request_timeout"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    NOT_ACCEPTABLE = "not_acceptable"
    PROVIDER_NOT_CONFIGURED = "provider_not_configured"
    PROVIDER_ERROR = "provider_error"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    AUDIO_EMPTY = "audio_empty"
    AUDIO_TOO_LARGE = "audio_too_large"
    AUDIO_TOO_LONG = "audio_too_long"
    AUDIO_UNSUPPORTED_TYPE = "audio_unsupported_type"
    AUDIO_SIGNATURE_MISMATCH = "audio_signature_mismatch"
    AUDIO_CORRUPT = "audio_corrupt"
    LANGUAGE_UNSUPPORTED = "language_unsupported"
    OUTBOUND_BLOCKED = "outbound_blocked"
    INTERNAL = "internal_error"


class ForgeError(Exception):
    """A client-safe error with a stable code and HTTP status."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int = 400,
        retryable: bool = False,
        details: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.retryable = retryable
        self.details = dict(details) if details else None
        self.headers = dict(headers) if headers else {}

    def to_payload(self, request_id: str | None = None) -> dict[str, Any]:
        from .security.redaction import redact

        body: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "request_id": request_id,
        }
        if self.retryable:
            body["retryable"] = True
        if self.details:
            body["details"] = redact(self.details)
        return {"error": body}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ForgeError(code={self.code!r}, http_status={self.http_status})"


class ValidationIssue:
    __slots__ = ("path", "message")

    def __init__(self, path: str, message: str) -> None:
        self.path = path
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "message": self.message}

    def __repr__(self) -> str:  # pragma: no cover
        return f"ValidationIssue({self.path!r}, {self.message!r})"


class ValidationFailed(ForgeError):
    """Raised when user-supplied structured input fails validation."""

    def __init__(self, issues: list[ValidationIssue], message: str = "Input validation failed.") -> None:
        super().__init__(
            ErrorCode.INVALID_ARGUMENT,
            message,
            http_status=422,
            details={"issues": [i.to_dict() for i in issues]},
        )
        self.issues = issues


class ConfigError(ValueError):
    """Operator-facing configuration problem (never sent to API clients)."""
