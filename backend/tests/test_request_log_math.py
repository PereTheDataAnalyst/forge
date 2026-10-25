"""Request log viewer helpers. Pure functions: no database, Redis or web framework needed."""

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta

from app.services import log_math

NOW = datetime(2026, 10, 20, 14, 37, 12, 345678, tzinfo=UTC)


def test_every_range_has_a_small_number_of_buckets():
    assert log_math.DEFAULT_RANGE in log_math.RANGES
    for name in log_math.RANGE_NAMES:
        since, until, step = log_math.window_for(name, NOW)
        count = len(log_math.bucket_indexes(since, until, step))
        assert 10 <= count <= 32, (name, count)
        assert until - since == log_math.RANGES[name][0]


def test_unknown_range_is_refused():
    try:
        log_math.window_for("2d", NOW)
    except ValueError:
        return
    raise AssertionError("an unknown range must raise")


def test_buckets_are_aligned_to_whole_utc_steps():
    hour = 3600
    index = log_math.bucket_index(NOW, hour)
    start = log_math.bucket_start(index, hour)
    assert start == datetime(2026, 10, 20, 14, 0, 0, tzinfo=UTC)
    assert log_math.bucket_index(start, hour) == index
    assert log_math.bucket_index(start - timedelta(microseconds=1), hour) == index - 1
    day = 86400
    assert log_math.bucket_start(log_math.bucket_index(NOW, day), day) == datetime(
        2026, 10, 20, tzinfo=UTC
    )


def test_a_naive_time_is_read_as_utc():
    naive = NOW.replace(tzinfo=None)
    assert log_math.bucket_index(naive, 3600) == log_math.bucket_index(NOW, 3600)


def test_fill_series_gives_every_bucket_a_point_and_keeps_found_values():
    since, until, step = log_math.window_for("24h", NOW)
    here = log_math.bucket_index(NOW, step)
    found = {
        here: {"requests": 5, "successful": 4, "avg_latency_ms": 900, "prompt_tokens": 50},
        here - 3: {"requests": 2, "successful": 2},
    }
    points = log_math.fill_series(found, since, until, step)
    assert len(points) == len(log_math.bucket_indexes(since, until, step))
    assert points[-1]["start"] == log_math.bucket_start(here, step)
    assert points[-1]["requests"] == 5 and points[-1]["failed"] == 1
    assert points[-1]["avg_latency_ms"] == 900 and points[-1]["prompt_tokens"] == 50
    assert points[-4]["requests"] == 2 and points[-4]["failed"] == 0
    quiet = points[0]
    assert quiet["requests"] == 0 and quiet["failed"] == 0 and quiet["avg_latency_ms"] is None
    starts = [p["start"] for p in points]
    assert starts == sorted(starts) and len(set(starts)) == len(starts)


def test_fill_series_ignores_buckets_outside_the_window():
    since, until, step = log_math.window_for("6h", NOW)
    far = log_math.bucket_index(NOW, step) + 50
    points = log_math.fill_series({far: {"requests": 9, "successful": 9}}, since, until, step)
    assert sum(p["requests"] for p in points) == 0


def test_number_conversions_cope_with_database_types():
    from decimal import Decimal

    assert log_math.to_ms(Decimal("1234.5")) == 1235
    assert log_math.to_ms(1234.4) == 1234
    assert log_math.to_ms(None) is None
    assert log_math.to_ms(float("nan")) is None
    assert log_math.to_ms(float("inf")) is None
    assert log_math.to_ms("not a number") is None
    assert log_math.to_int(None) == 0
    assert log_math.to_int(Decimal("7")) == 7
    assert log_math.to_int("x") == 0


def test_success_rate():
    assert log_math.success_rate(0, 0) is None
    assert log_math.success_rate(8, 7) == 0.875
    assert log_math.success_rate(3, 1) == 0.3333


def test_cursor_round_trips_and_keeps_microseconds():
    row_id = uuid.uuid4()
    text = log_math.encode_cursor(NOW, row_id)
    assert "=" not in text and text.isascii()
    moment, found_id = log_math.decode_cursor(text)
    assert moment == NOW and found_id == row_id


def test_cursor_from_a_naive_time_is_read_as_utc():
    row_id = uuid.uuid4()
    moment, _ = log_math.decode_cursor(log_math.encode_cursor(NOW.replace(tzinfo=None), row_id))
    assert moment == NOW


def _forge(payload) -> str:
    raw = json.dumps(payload).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def test_bad_cursors_are_refused():
    good_id = str(uuid.uuid4())
    bad = [
        "",
        "!!!",
        "a" * 500,
        _forge([1, 2]),
        _forge({"t": "yesterday", "i": good_id}),
        _forge({"t": NOW.isoformat(), "i": "not-a-uuid"}),
        _forge({"t": NOW.isoformat()}),
        _forge({"t": "2026-10-20T14:00:00", "i": good_id}),  # no time zone
        base64.urlsafe_b64encode(b"\xff\xfe\x00").decode(),
    ]
    for text in bad:
        try:
            log_math.decode_cursor(text)
        except ValueError:
            continue
        raise AssertionError(f"accepted a bad cursor: {text[:30]!r}")


def test_request_id_search_text_is_restricted():
    assert log_math.clean_request_id("  req_3f9a1c0d  ") == "req_3f9a1c0d"
    for bad in ["", "   ", "req 1", "req%", "a" * 65, "req_\u00e9", "x;drop", "a/b"]:
        try:
            log_math.clean_request_id(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad!r}")
