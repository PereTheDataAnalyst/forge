"""Translation between the evaluation library's errors and the platform's errors."""

from forge_integrations.errors import ForgeError, ValidationFailed

from app.core.errors import AppError, UnprocessableEntity

# HTTP statuses where trying again later can succeed.
_RETRYABLE_STATUSES = frozenset({429, 502, 503, 504})

MAX_REPORTED_ISSUES = 50


def to_forge_error(exc: AppError) -> ForgeError:
    """A platform error as the library's error, so the engine can record it on a case."""
    return ForgeError(
        exc.code,
        exc.message,
        http_status=exc.status_code,
        retryable=exc.status_code in _RETRYABLE_STATUSES,
    )


def to_app_error(exc: ForgeError) -> AppError:
    """A library error as the platform's error, so the API answers in the standard shape."""
    if isinstance(exc, ValidationFailed):
        return UnprocessableEntity(
            "The dataset has problems. Fix the listed fields and try again.",
            code="invalid_dataset",
            details=[
                {"field": issue.path, "message": issue.message}
                for issue in exc.issues[:MAX_REPORTED_ISSUES]
            ],
        )
    return AppError(exc.message, code=exc.code, status_code=exc.http_status, headers=exc.headers)
