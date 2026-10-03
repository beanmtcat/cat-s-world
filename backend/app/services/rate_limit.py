"""Small Redis-backed fixed-window limits for abuse-prone public actions."""

from __future__ import annotations

import hashlib
import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.core.errors import AppError

_client: Redis | None = None
logger = logging.getLogger("mmcat.rate_limit")

# Redis 6 does not support EXPIRE ... NX. Keep the counter and its expiry
# atomic, and discard no-expiry counters left by the old failed pipeline.
_INCREMENT_WITH_EXPIRY = """
if redis.call('TTL', KEYS[1]) == -1 then
    redis.call('DEL', KEYS[1])
end
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""


def _redis() -> Redis:
    global _client
    if _client is None:
        _client = Redis.from_url(get_settings().redis_url, decode_responses=True)
    return _client


def limiter_key(scope: str, value: str) -> str:
    """Do not place account names or raw IPs in Redis keys."""
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"{get_settings().redis_key_prefix}:rate:{scope}:{digest}"


async def enforce_rate_limit(scope: str, value: str, *, limit: int, window_seconds: int) -> None:
    key = limiter_key(scope, value)
    try:
        count = await _redis().eval(_INCREMENT_WITH_EXPIRY, 1, key, window_seconds)
    except RedisError as exc:
        # Failing open makes credential stuffing possible during an outage;
        # protected write endpoints should explicitly fail closed instead.
        logger.exception("Rate limiter unavailable for %s", scope)
        raise AppError("RATE_LIMIT_UNAVAILABLE", "服务保护暂不可用，请稍后重试。", 503) from exc
    if int(count) > limit:
        raise AppError("RATE_LIMITED", "操作过于频繁，请稍后再试。", 429)
