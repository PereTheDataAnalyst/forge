"""Redis-backed fixed-window rate limiting and failure counters."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.errors import RateLimited, ServiceUnavailable
from app.core.logging import get_logger

log = get_logger(__name__)

# Atomically increments the counter, starts the window on the first hit and returns
# {count, seconds_until_reset}. The TTL guard repairs a key that lost its expiry.
_HIT_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('TTL', KEYS[1])
if ttl < 0 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
  ttl = tonumber(ARGV[1])
end
return {current, ttl}
"""


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset_seconds: int

    def headers(self) -> dict[str, str]:
        return {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(self.remaining),
            "X-RateLimit-Reset": str(self.reset_seconds),
        }


class RateLimiter:
    def __init__(self, redis: Redis, *, fail_open: bool = False) -> None:
        self._redis = redis
        self._fail_open = fail_open
        self._hit_script = redis.register_script(_HIT_SCRIPT)

    async def _safely(self, operation: Callable[[], Awaitable[Any]], fallback: Any) -> Any:
        try:
            return await operation()
        except (RedisError, OSError, TimeoutError) as exc:
            log.error("rate_limiter.redis_error", extra={"error_type": type(exc).__name__})
            if self._fail_open:
                return fallback
            raise ServiceUnavailable(
                "Rate limiting is temporarily unavailable. Try again shortly.",
                code="rate_limiter_unavailable",
            ) from exc

    async def hit(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        raw = await self._safely(
            lambda: self._hit_script(keys=[key], args=[window_seconds]), [0, window_seconds]
        )
        current, ttl = int(raw[0]), max(int(raw[1]), 1)
        return RateLimitResult(
            allowed=current <= limit,
            limit=limit,
            remaining=max(limit - current, 0),
            reset_seconds=ttl,
        )

    async def enforce(
        self,
        key: str,
        limit: int,
        window_seconds: int,
        *,
        code: str = "rate_limited",
        message: str | None = None,
    ) -> RateLimitResult:
        result = await self.hit(key, limit, window_seconds)
        if not result.allowed:
            raise RateLimited(
                message or "Too many requests. Retry after the indicated delay.",
                code=code,
                headers={**result.headers(), "Retry-After": str(result.reset_seconds)},
            )
        return result

    async def count(self, key: str) -> int:
        value = await self._safely(lambda: self._redis.get(key), None)
        return int(value) if value is not None else 0

    async def increment(self, key: str, window_seconds: int) -> int:
        raw = await self._safely(
            lambda: self._hit_script(keys=[key], args=[window_seconds]), [0, window_seconds]
        )
        return int(raw[0])

    async def ttl(self, key: str) -> int:
        value = await self._safely(lambda: self._redis.ttl(key), -2)
        return max(int(value), 0)

    async def reset(self, key: str) -> None:
        await self._safely(lambda: self._redis.delete(key), 0)
