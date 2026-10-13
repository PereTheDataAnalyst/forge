"""Request ids and structured event logging with redaction."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Mapping

from .security.redaction import redact


def new_request_id() -> str:
    return "req_" + uuid.uuid4().hex


class JsonLogObserver:
    """Writes one redacted JSON line per event through the standard ``logging`` module."""

    def __init__(self, logger: logging.Logger | None = None, level: int = logging.INFO) -> None:
        self._logger = logger or logging.getLogger("forge.integrations")
        self._level = level

    def emit(self, event: Mapping[str, Any]) -> None:
        try:
            self._logger.log(self._level, json.dumps(redact(event), ensure_ascii=False, default=str, sort_keys=True))
        except Exception:  # noqa: BLE001 - logging must never break a request
            pass


class NullObserver:
    def emit(self, event: Mapping[str, Any]) -> None:
        return None
