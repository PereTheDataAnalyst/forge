"""In-process sliding-window rate limiter.

This satisfies the ``RateLimiter`` port for a single server process. When the MCP server runs
as several processes, or when the platform already provides a shared limiter (Redis), pass that
implementation instead; the port is the same.

Memory is bounded twice: a key never holds more timestamps than its limit, and the number of
keys is capped (expired keys are dropped first, then the least recently used).
"""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict, deque
from typing import Callable

from ..ports import RateDecision


class InMemoryRateLimiter:
    def __init__(self, *, max_keys: int = 50_000, clock: Callable[[], float] = time.monotonic) -> None:
        if max_keys < 1:
            raise ValueError("max_keys must be positive")
        self._max_keys = max_keys
        self._clock = clock
        self._lock = threading.Lock()
        # key -> (window_seconds, timestamps of allowed requests, oldest first)
        self._hits: OrderedDict[str, tuple[int, deque[float]]] = OrderedDict()

    async def check(self, key: str, limit: int, window_seconds: int) -> RateDecision:
        if limit < 1 or window_seconds < 1:
            raise ValueError("limit and window_seconds must be positive")
        now = self._clock()
        with self._lock:
            entry = self._hits.get(key)
            if entry is None:
                self._make_room(now)
                stamps: deque[float] = deque()
                self._hits[key] = (window_seconds, stamps)
            else:
                stamps = entry[1]
                self._hits[key] = (window_seconds, stamps)
                self._hits.move_to_end(key)
            horizon = now - window_seconds
            while stamps and stamps[0] <= horizon:
                stamps.popleft()
            if len(stamps) >= limit:
                retry = max(1, math.ceil(stamps[0] + window_seconds - now))
                return RateDecision(allowed=False, remaining=0, retry_after_seconds=retry)
            stamps.append(now)
            return RateDecision(allowed=True, remaining=limit - len(stamps), retry_after_seconds=0)

    def _make_room(self, now: float) -> None:
        if len(self._hits) < self._max_keys:
            return
        expired = [
            key
            for key, (window, stamps) in self._hits.items()
            if not stamps or stamps[-1] <= now - window
        ]
        for key in expired:
            del self._hits[key]
        while len(self._hits) >= self._max_keys:
            self._hits.popitem(last=False)

    def __len__(self) -> int:
        return len(self._hits)
