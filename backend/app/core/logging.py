"""Structured logging with automatic redaction of secrets."""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from app.core.redaction import redact_mapping, redact_text
from app.core.request_context import get_request_id

_STANDARD_ATTRS = frozenset(
    logging.LogRecord("x", 0, "x", 0, "x", (), None).__dict__.keys()
) | {"message", "asctime", "taskName", "request_id"}


class RequestContextFilter(logging.Filter):
    """Adds the current request ID to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


class ForgeFormatter(logging.Formatter):
    def __init__(self, json_output: bool) -> None:
        super().__init__()
        self._json_output = json_output

    @staticmethod
    def _extras(record: logging.LogRecord) -> dict[str, Any]:
        return {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STANDARD_ATTRS and not key.startswith("_")
        }

    def format(self, record: logging.LogRecord) -> str:
        message = redact_text(record.getMessage())
        extras = redact_mapping(self._extras(record))
        request_id = getattr(record, "request_id", None)
        exception = (
            redact_text(self.formatException(record.exc_info)) if record.exc_info else None
        )
        timestamp = datetime.fromtimestamp(record.created, UTC)

        if self._json_output:
            payload: dict[str, Any] = {
                "ts": timestamp.isoformat(timespec="milliseconds"),
                "level": record.levelname,
                "logger": record.name,
                "message": message,
            }
            if request_id:
                payload["request_id"] = request_id
            payload.update(extras)
            if exception:
                payload["exception"] = exception
            return json.dumps(payload, default=str)

        parts = [
            timestamp.strftime("%H:%M:%S"),
            f"{record.levelname:<7}",
            record.name,
            message,
        ]
        if request_id:
            parts.append(f"request_id={request_id}")
        parts.extend(f"{key}={value}" for key, value in extras.items())
        line = " ".join(str(part) for part in parts)
        return f"{line}\n{exception}" if exception else line


def configure_logging(level: str = "INFO", log_format: str = "json") -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(ForgeFormatter(json_output=log_format == "json"))
    handler.addFilter(RequestContextFilter())
    root.addHandler(handler)
    root.setLevel(level)

    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False
    for noisy in ("sqlalchemy.engine", "httpx", "httpcore", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
