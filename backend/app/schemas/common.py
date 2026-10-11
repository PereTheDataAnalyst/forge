"""Shared response models used for OpenAPI documentation of errors."""

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    field: str
    message: str


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    details: list[ErrorDetail] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


ERROR_RESPONSES: dict[int | str, dict] = {
    401: {"model": ErrorResponse, "description": "Authentication is missing or invalid."},
    403: {"model": ErrorResponse, "description": "The caller is not allowed to do this."},
    404: {"model": ErrorResponse, "description": "The resource does not exist."},
    422: {"model": ErrorResponse, "description": "The request is not valid."},
    429: {"model": ErrorResponse, "description": "Rate limit exceeded."},
}
