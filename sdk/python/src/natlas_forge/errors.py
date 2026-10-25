"""Errors raised by the client. Every one that comes from the server carries its request ID."""

from __future__ import annotations

from typing import Any, Mapping, Optional


class ForgeError(Exception):
    """Base class for every error this package raises on purpose.

    Attributes: ``message`` (safe to show), ``status`` (HTTP status, or None if no response),
    ``code`` (the API's stable error code, for example ``rate_limited``), ``request_id`` (quote it
    when you contact support or look the request up in the request logs), ``details`` (field
    errors for a 422) and ``retry_after`` (seconds, when the server said how long to wait).
    """

    def __init__(
        self,
        message: str,
        *,
        status: Optional[int] = None,
        code: Optional[str] = None,
        request_id: Optional[str] = None,
        details: Optional[Any] = None,
        retry_after: Optional[float] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.request_id = request_id
        self.details = details
        self.retry_after = retry_after

    def __str__(self) -> str:
        parts = []
        if self.code:
            parts.append(f"code={self.code}")
        if self.status is not None:
            parts.append(f"status={self.status}")
        if self.request_id:
            parts.append(f"request_id={self.request_id}")
        return f"{self.message} ({', '.join(parts)})" if parts else self.message


class APIConnectionError(ForgeError):
    """The server could not be reached (DNS, refused connection, TLS, reset)."""


class APITimeoutError(APIConnectionError):
    """The server did not answer within the timeout."""


class APIStatusError(ForgeError):
    """The server answered with an error status."""


class InvalidRequestError(APIStatusError):
    """400, 413, 415 or 422: the request itself was refused. Fix it before trying again."""


class AuthenticationError(APIStatusError):
    """401: the API key is missing, wrong, revoked or expired."""


class PermissionDeniedError(APIStatusError):
    """403: the key lacks the scope, or the project is archived."""


class NotFoundError(APIStatusError):
    """404: for example an unknown model id."""


class RateLimitError(APIStatusError):
    """429: slow down. ``retry_after`` says how long to wait when the server told us."""


class ServerError(APIStatusError):
    """5xx: the platform or the model host failed. Often worth retrying later."""


def error_from_response(
    status: int, body: Optional[Mapping[str, Any]], headers: Mapping[str, str]
) -> APIStatusError:
    """Build the right error from an error response. Never includes request content."""
    info = body.get("error") if isinstance(body, Mapping) else None
    info = info if isinstance(info, Mapping) else {}
    message = str(info.get("message") or f"The server answered with status {status}.")
    code = info.get("code")
    request_id = info.get("request_id") or headers.get("x-request-id")
    retry_after = parse_retry_after(headers.get("retry-after"))
    kwargs = dict(
        status=status,
        code=str(code) if code else None,
        request_id=str(request_id) if request_id else None,
        details=info.get("details"),
        retry_after=retry_after,
    )
    if status == 401:
        return AuthenticationError(message, **kwargs)
    if status == 403:
        return PermissionDeniedError(message, **kwargs)
    if status == 404:
        return NotFoundError(message, **kwargs)
    if status == 429:
        return RateLimitError(message, **kwargs)
    if status >= 500:
        return ServerError(message, **kwargs)
    return InvalidRequestError(message, **kwargs)


def parse_retry_after(value: Optional[str]) -> Optional[float]:
    """Seconds from a Retry-After header that holds a number. Dates are not used by this API."""
    if value is None:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return seconds if 0 <= seconds < 86_400 else None
