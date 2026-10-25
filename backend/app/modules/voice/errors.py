"""Translation between the voice library's errors and the platform's errors."""

from forge_integrations.errors import ForgeError

from app.core.errors import AppError


def to_app_error(exc: ForgeError) -> AppError:
    """A library error as the platform's error, so the API answers in the standard shape."""
    return AppError(exc.message, code=exc.code, status_code=exc.http_status, headers=exc.headers)
