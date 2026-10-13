"""Bounded work admission for blocking jobs.

At most ``concurrency`` jobs run at once and at most ``max_waiting`` more may queue. Beyond
that callers are refused immediately with a retryable error instead of piling up. A permit
is released only when the thread has actually finished, even when the caller timed out, so
the concurrency bound is a real bound on CPU and memory.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, TypeVar

from ..errors import ErrorCode, ForgeError

T = TypeVar("T")


class WorkGate:
    def __init__(self, concurrency: int, max_waiting: int) -> None:
        if concurrency < 1 or max_waiting < 0:
            raise ValueError("concurrency must be >= 1 and max_waiting >= 0")
        self._concurrency = concurrency
        self._max_waiting = max_waiting
        self._semaphore = asyncio.Semaphore(concurrency)
        self._waiting = 0
        self._running = 0

    @property
    def waiting(self) -> int:
        return self._waiting

    @property
    def running(self) -> int:
        return self._running

    async def run(self, func: Callable[..., T], *args: Any, timeout: float) -> T:
        if self._running >= self._concurrency and self._waiting >= self._max_waiting:
            raise ForgeError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "The ASR worker is at capacity. Retry shortly.",
                http_status=503,
                retryable=True,
                headers={"Retry-After": "5"},
            )
        self._waiting += 1
        try:
            await self._semaphore.acquire()
        finally:
            self._waiting -= 1
        self._running += 1

        task = asyncio.ensure_future(asyncio.to_thread(func, *args))

        def _finished(done: "asyncio.Future[Any]") -> None:
            self._running -= 1
            self._semaphore.release()
            if not done.cancelled():
                done.exception()  # consume it so an abandoned job never logs "never retrieved"

        task.add_done_callback(_finished)
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout)
        except asyncio.TimeoutError:
            raise ForgeError(
                ErrorCode.PROVIDER_TIMEOUT,
                "The ASR worker did not finish in time.",
                http_status=504,
                retryable=True,
            ) from None
