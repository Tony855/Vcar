import pytest
from fastapi import HTTPException

from app.web.login_rate_limit import (
    _keys,
    clear_identity_login_attempts,
    consume_login_attempt,
)


class FakeRedis:
    def __init__(self, ip_count=1, identity_count=1):
        self.ip_count = ip_count
        self.identity_count = identity_count
        self.eval_calls = []
        self.deleted = []

    async def eval(self, script, numkeys, ip_key, identity_key, window):
        self.eval_calls.append((script, numkeys, ip_key, identity_key, window))
        return [self.ip_count, self.identity_count, 900]

    async def delete(self, key):
        self.deleted.append(key)


@pytest.mark.asyncio
async def test_login_attempt_uses_hashed_keys_and_atomic_15_minute_window():
    redis = FakeRedis()
    await consume_login_attempt(redis, "203.0.113.8", "admin")
    _, numkeys, ip_key, identity_key, window = redis.eval_calls[0]
    assert numkeys == 2
    assert window == 900
    assert "203.0.113.8" not in ip_key + identity_key
    assert "admin" not in ip_key + identity_key
    assert ip_key.startswith("vcar:auth:login:ip:")
    assert identity_key.startswith("vcar:auth:login:identity:")


@pytest.mark.asyncio
async def test_login_attempt_blocks_after_five_attempts_for_same_identity():
    redis = FakeRedis(identity_count=6)
    with pytest.raises(HTTPException) as error:
        await consume_login_attempt(redis, "203.0.113.8", "admin")
    assert error.value.status_code == 429
    assert error.value.headers["Retry-After"] == "900"


@pytest.mark.asyncio
async def test_login_attempt_blocks_after_twenty_attempts_per_ip():
    redis = FakeRedis(ip_count=21, identity_count=1)
    with pytest.raises(HTTPException) as error:
        await consume_login_attempt(redis, "203.0.113.8", "other-user")
    assert error.value.status_code == 429


@pytest.mark.asyncio
async def test_successful_login_clears_only_identity_counter():
    redis = FakeRedis()
    _, identity_key = _keys("203.0.113.8", "admin")
    await clear_identity_login_attempts(redis, "203.0.113.8", "admin")
    assert redis.deleted == [identity_key]


@pytest.mark.asyncio
async def test_redis_errors_are_not_silently_ignored():
    class BrokenRedis:
        async def eval(self, *args):
            raise RuntimeError("redis unavailable")

    with pytest.raises(RuntimeError, match="redis unavailable"):
        await consume_login_attempt(BrokenRedis(), "203.0.113.8", "admin")
