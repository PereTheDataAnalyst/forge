"""Structured application errors and their HTTP handlers.

Every error response has the same shape:
  {"error": {"code": "...", "message": "...", "request_id": "...", "details": [...]}}
Stack traces, SQL errors, internal paths and secrets never appear in responses.
"""

from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger
from app.core.request_context import get_request_id

log = get_logger(__name__)


class AppError(Exception):
    status_code: int = 500
    code: str = "internal_error"
    message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        headers: Mapping[str, str] | None = None,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message or self.message)
        if message is not None:
            self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.headers: dict[str, str] = dict(headers) if headers else {}
        self.details = details


class BadRequest(AppError):
    status_code = 400
    code = "bad_request"
    message = "The request could not be processed."


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"
    message = "Authentication is required."


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"
    message = "You do not have permission to perform this action."


class NotFound(AppError):
    status_code = 404
    code = "not_found"
    message = "The requested resource was not found."


class Conflict(AppError):
    status_code = 409
    code = "conflict"
    message = "The request conflicts with the current state of the resource."


class PayloadTooLarge(AppError):
    status_code = 413
    code = "payload_too_large"
    message = "The request body is too large."


class UnprocessableEntity(AppError):
    status_code = 422
    code = "unprocessable_entity"
    message = "The request is well formed but cannot be processed."


class RateLimited(AppError):
    status_code = 429
    code = "rate_limited"
    message = "Too many requests. Slow down and retry later."


class BadGateway(AppError):
    status_code = 502
    code = "bad_gateway"
    message = "An upstream service returned an invalid response."


class ServiceUnavailable(AppError):
    status_code = 503
    code = "service_unavailable"
    message = "The service is temporarily unavailable."


class GatewayTimeout(AppError):
    status_code = 504
    code = "gateway_timeout"
    message = "An upstream service did not respond in time."


def error_payload(
    code: str, message: str, details: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message, "request_id": get_request_id()}
    if details:
        body["details"] = details
    return {"error": body}


def error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    headers: Mapping[str, str] | None = None,
    details: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    return JSONResponse(
        error_payload(code, message, details),
        status_code=status_code,
        headers=dict(headers) if headers else None,
    )


async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    if exc.status_code >= 500:
        log.error("app_error", extra={"code": exc.code, "status": exc.status_code})
    return error_response(
        exc.status_code, exc.code, exc.message, headers=exc.headers, details=exc.details
    )


async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    details: list[dict[str, Any]] = []
    for err in exc.errors():
        location = [str(part) for part in err.get("loc", ()) if part != "body"]
        details.append(
            {"field": ".".join(location) or "body", "message": str(err.get("msg", "Invalid value"))}
        )
    return error_response(
        422,
        "validation_error",
        "The request is not valid. See details for the fields to fix.",
        details=details[:20],
    )


_HTTP_CODES = {
    400: ("bad_request", "The request could not be processed."),
    401: ("unauthorized", "Authentication is required."),
    403: ("forbidden", "You do not have permission to perform this action."),
    404: ("not_found", "The requested resource was not found."),
    405: ("method_not_allowed", "This method is not allowed for the resource."),
    413: ("payload_too_large", "The request body is too large."),
    415: ("unsupported_media_type", "The content type is not supported."),
    429: ("rate_limited", "Too many requests. Slow down and retry later."),
}


async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code, message = _HTTP_CODES.get(exc.status_code, ("http_error", "The request failed."))
    return error_response(exc.status_code, code, message, headers=exc.headers)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, handle_app_error)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, handle_validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)  # type: ignore[arg-type]
