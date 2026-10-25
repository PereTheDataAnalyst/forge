"""Pure helpers for the request log viewer: time ranges, chart buckets and paging cursors.

Nothing here reads the clock or the database on its own, so every function can be tested
directly. The SQL that uses them lives in `request_log_service`.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

# Range name -> (how far back the window reaches, seconds per chart bucket). Buckets are aligned
# to the Unix epoch, so they start on a whole UTC hour or day. A window holds at most 31 buckets.
RANGES: dict[str, tuple[timedelta, int]] = {
    "1h": (timedelta(hours=1), 300),
    "6h": (timedelta(hours=6), 900),
    "24h": (timedelta(hours=24), 3600),
    "7d": (timedelta(days=7), 21600),
    "30d": (timedelta(days=30), 86400),
    "90d": (timedelta(days=90), 259200),
}
RANGE_NAMES: tuple[str, ...] = tuple(RANGES)
DEFAULT_RANGE = "24h"
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
MAX_CURSOR_CHARS = 200

_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

EMPTY_POINT: dict[str, Any] = {
    "requests": 0,
    "successful": 0,
    "failed": 0,
    "avg_latency_ms": None,
    "p50_latency_ms": None,
    "p95_latency_ms": None,
    "prompt_tokens": 0,
    "completion_tokens": 0,
}


def _aware(moment: datetime) -> datetime:
    """A naive datetime is taken as UTC. An aware one is converted to UTC."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def window_for(range_name: str, now: datetime) -> tuple[datetime, datetime, int]:
    """Return (since, until, bucket_seconds) for a named range that ends at `now`."""
    try:
        span, step = RANGES[range_name]
    except KeyError:
        raise ValueError(f"unknown range {range_name!r}") from None
    until = _aware(now)
    return until - span, until, step


def bucket_index(moment: datetime, step: int) -> int:
    """The number of the bucket that holds `moment`: whole steps since the Unix epoch."""
    return math.floor(_aware(moment).timestamp() / step)


def bucket_start(index: int, step: int) -> datetime:
    return datetime.fromtimestamp(index * step, tz=UTC)


def bucket_indexes(since: datetime, until: datetime, step: int) -> list[int]:
    """Every bucket number from the one holding `since` to the one holding `until`."""
    first = bucket_index(since, step)
    last = bucket_index(until, step)
    return list(range(first, last + 1))


def to_ms(value: Any) -> int | None:
    """A database number (Decimal, float or int) as whole milliseconds, or None."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return int(math.floor(number + 0.5))


def to_int(value: Any) -> int:
    """A database count as an int. A missing value counts as zero."""
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def success_rate(requests: int, successful: int) -> float | None:
    if requests <= 0:
        return None
    return round(successful / requests, 4)


def fill_series(
    found: dict[int, dict[str, Any]], since: datetime, until: datetime, step: int
) -> list[dict[str, Any]]:
    """Give every bucket in the window a point, with zeros where nothing was recorded."""
    points: list[dict[str, Any]] = []
    for index in bucket_indexes(since, until, step):
        point = dict(EMPTY_POINT)
        point.update(found.get(index, {}))
        point["failed"] = point["requests"] - point["successful"]
        point["start"] = bucket_start(index, step)
        points.append(point)
    return points


def encode_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    """An opaque paging cursor that names the last row of a page."""
    payload = {"t": _aware(created_at).isoformat(), "i": str(row_id)}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(text: str) -> tuple[datetime, uuid.UUID]:
    """Read a cursor made by `encode_cursor`. Raises ValueError for anything else."""
    if not text or len(text) > MAX_CURSOR_CHARS:
        raise ValueError("invalid cursor")
    try:
        padded = text + "=" * (-len(text) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        moment = datetime.fromisoformat(data["t"])
        row_id = uuid.UUID(data["i"])
    except (ValueError, KeyError, TypeError, binascii.Error, UnicodeError) as exc:
        raise ValueError("invalid cursor") from exc
    if moment.tzinfo is None:
        raise ValueError("invalid cursor")
    return moment.astimezone(UTC), row_id


def clean_request_id(value: str) -> str:
    """Validate the text of a request ID search. Raises ValueError for anything unusual."""
    text = value.strip()
    if not _REQUEST_ID.fullmatch(text):
        raise ValueError("invalid request id")
    return text
