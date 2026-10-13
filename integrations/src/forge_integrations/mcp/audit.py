"""Audit events for MCP.

Events describe who did what and how it ended. They never contain prompts, model output,
transcripts, audio, tokens or argument values: only names, sizes, codes and timings. Every
event is redacted before it leaves this module as a second line of defence.

A failing sink never fails the request; the failure is reported through the observer.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..observability import NullObserver
from ..ports import AuditSink, Observer
from ..security.redaction import redact

EVENT_TOOL_CALL = "mcp.tool_call"
EVENT_AUTH_FAILURE = "mcp.auth_failure"
EVENT_AUTH_THROTTLED = "mcp.auth_throttled"
EVENT_REQUEST_LIMITED = "mcp.request_limited"
EVENT_TOKEN_ISSUED = "mcp.token_issued"
EVENT_TOKEN_REVOKED = "mcp.token_revoked"
EVENT_POLICY_DENIED = "mcp.policy_denied"
EVENT_AUDIT_FAILURE = "mcp.audit_failure"

OUTCOME_OK = "ok"
OUTCOME_ERROR = "error"
OUTCOME_DENIED = "denied"
OUTCOME_RATE_LIMITED = "rate_limited"
OUTCOME_INVALID = "invalid"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryAuditSink:
    """A bounded in-memory sink for tests and single-process demos. Not durable."""

    def __init__(self, max_events: int = 10_000) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)

    async def record(self, event: Mapping[str, Any]) -> None:
        self._events.append(dict(event))

    @property
    def events(self) -> list[dict[str, Any]]:
        return list(self._events)


class JsonlAuditSink:
    """Appends one JSON line per event to a file that only its owner can read.

    For standalone deployments. A platform integration supplies a sink backed by its own database
    instead. The file is rotated once, to ``<name>.1``, when it reaches ``max_bytes``, so it cannot
    grow without bound; keep or ship the rotated copy if you need a longer history.
    """

    def __init__(self, path: str | os.PathLike[str], *, max_bytes: int = 50 * 1024 * 1024) -> None:
        if max_bytes < 1024:
            raise ValueError("max_bytes must be at least 1024")
        self._path = Path(path)
        self._max_bytes = max_bytes
        parent_existed = self._path.parent.exists()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not parent_existed and os.name == "posix":
            try:
                os.chmod(self._path.parent, 0o700)
            except OSError:
                pass
        self._lock = threading.Lock()
        self._append("")  # create the file now, owner-only, so a bad path fails at startup

    async def record(self, event: Mapping[str, Any]) -> None:
        line = json.dumps(dict(event), ensure_ascii=False, separators=(",", ":"), default=str) + "\n"
        await asyncio.to_thread(self._append, line)

    def _append(self, line: str) -> None:
        with self._lock:
            try:
                if self._path.stat().st_size >= self._max_bytes:
                    os.replace(self._path, self._path.with_name(self._path.name + ".1"))
            except FileNotFoundError:
                pass
            fd = os.open(self._path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                if line:
                    os.write(fd, line.encode("utf-8"))
            finally:
                os.close(fd)


class AuditRecorder:
    def __init__(
        self,
        sink: AuditSink | None = None,
        observer: Observer | None = None,
        *,
        clock=utc_now,
    ) -> None:
        self._sink = sink
        self._observer: Observer = observer or NullObserver()
        self._clock = clock

    def _emit(self, event: dict[str, Any]) -> None:
        try:
            self._observer.emit(event)
        except Exception:  # noqa: BLE001 - observability must never break a request
            pass

    async def record(self, event_type: str, **fields: Any) -> None:
        event = redact({"event": event_type, "at": self._clock().isoformat(), **fields})
        self._emit(event)
        if self._sink is None:
            return
        try:
            await self._sink.record(event)
        except Exception as exc:  # noqa: BLE001 - never fail the caller because auditing failed
            self._emit({"event": EVENT_AUDIT_FAILURE, "sink_error": type(exc).__name__, "failed_event": event_type})


def monotonic_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)
