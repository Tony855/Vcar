import pytest

from app.services.driver_presence import is_driver_online


class FakeRedis:
    def __init__(self, score):
        self.score = score
        self.calls = []

    async def zscore(self, key, member):
        self.calls.append((key, member))
        return self.score


@pytest.mark.asyncio
@pytest.mark.parametrize("score,now,expected", [
    (990.0, 1000.0, True),
    (880.0, 1000.0, True),
    (879.9, 1000.0, False),
    (1001.0, 1000.0, False),
    (None, 1000.0, False),
    (float("nan"), 1000.0, False),
    (float("inf"), 1000.0, False),
    ("not-a-timestamp", 1000.0, False),
])
async def test_driver_online_requires_finite_fresh_heartbeat(score, now, expected):
    redis = FakeRedis(score)

    assert await is_driver_online(redis, 7002002, now=now) is expected
    assert redis.calls == [("driver:heartbeat", "7002002")]


@pytest.mark.asyncio
async def test_driver_online_rejects_future_or_invalid_clock():
    redis = FakeRedis(990.0)

    assert await is_driver_online(redis, "7002002", now=float("nan")) is False
    assert redis.calls == [("driver:heartbeat", "7002002")]


@pytest.mark.asyncio
async def test_driver_online_supports_configured_ttl():
    redis = FakeRedis(950.0)

    assert await is_driver_online(redis, "7002002", now=1000.0, ttl_seconds=30) is False
