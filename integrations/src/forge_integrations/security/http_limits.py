"""Bounded reads. Never read an unbounded body into memory."""

from __future__ import annotations

from typing import Callable

from ..errors import ErrorCode, ForgeError


def read_all_limited(read: Callable[[int], bytes], max_bytes: int, *, chunk_size: int = 65536) -> bytes:
    buffer = bytearray()
    while True:
        want = min(chunk_size, max_bytes + 1 - len(buffer))
        chunk = read(want)
        if not chunk:
            break
        buffer += chunk
        if len(buffer) > max_bytes:
            raise ForgeError(
                ErrorCode.PAYLOAD_TOO_LARGE,
                f"The payload exceeds the {max_bytes} byte limit.",
                http_status=413,
            )
    return bytes(buffer)
