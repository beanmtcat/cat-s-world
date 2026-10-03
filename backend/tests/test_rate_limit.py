from __future__ import annotations

from types import SimpleNamespace

import pytest
from redis.exceptions import RedisError

from app.core.errors import AppError
from app.services import rate_limit


class FakeRedis:
    def __init__(self, result: int | RedisError) -> None:
        self.result = result
        self.calls: list[tuple[str, int, str, int]] = []

    async def eval(self, script: str, key_count: int, key: str, window: int) -> int:
        self.calls.append((script, key_count, key, window))
        if isinstance(self.result, RedisError):
            raise self.result
        return self.result


def test_rate_limit_key_uses_configured_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rate_limit, "get_settings", lambda: SimpleNamespace(redis_key_prefix="custom-site"))
    assert rate_limit.limiter_key("login-ip", "192.0.2.1").startswith("custom-site:rate:login-ip:")


async def test_rate_limit_uses_atomic_redis_6_compatible_script(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = FakeRedis(2)
    monkeypatch.setattr(rate_limit, "_redis", lambda: redis)

    await rate_limit.enforce_rate_limit("login-ip", "192.0.2.1", limit=2, window_seconds=60)

    script, key_count, key, window = redis.calls[0]
    assert key_count == 1
    assert key == rate_limit.limiter_key("login-ip", "192.0.2.1")
    assert "192.0.2.1" not in key
    assert window == 60
    assert "'INCR'" in script and "'EXPIRE'" in script
    assert "NX" not in script


async def test_rate_limit_rejects_excess_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rate_limit, "_redis", lambda: FakeRedis(3))

    with pytest.raises(AppError) as error:
        await rate_limit.enforce_rate_limit("login-ip", "192.0.2.1", limit=2, window_seconds=60)
    assert error.value.status_code == 429


async def test_rate_limit_fails_closed_on_redis_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rate_limit, "_redis", lambda: FakeRedis(RedisError("Redis unavailable")))

    with pytest.raises(AppError) as error:
        await rate_limit.enforce_rate_limit("login-ip", "192.0.2.1", limit=2, window_seconds=60)
    assert error.value.status_code == 503
