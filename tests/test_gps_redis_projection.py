import pytest

from app.modules.locations.redis_projection import publish_driver_location


class FakeRedis:
    def __init__(self, fail_on=None):
        self.fail_on = fail_on
        self.calls = []

    async def _record(self, name, *args):
        self.calls.append((name, args))
        if self.fail_on == name:
            raise ConnectionError("simulated Redis outage")

    async def geoadd(self, *args):
        await self._record("geoadd", *args)

    async def zadd(self, *args):
        await self._record("zadd", *args)

    async def zremrangebyscore(self, *args):
        await self._record("zremrangebyscore", *args)

    async def publish(self, *args):
        await self._record("publish", *args)


@pytest.fixture
def sample():
    return {
        "telegram_id": 12345,
        "latitude": 11.5564,
        "longitude": 104.9282,
        "accuracy_m": 8.0,
        "heading_deg": 90.0,
        "speed_mps": 4.0,
        "recorded_at": "2026-11-12T05:00:00+00:00",
    }


@pytest.mark.asyncio
async def test_projection_updates_presence_and_both_streams(sample):
    redis = FakeRedis()

    result = await publish_driver_location(
        redis,
        telegram_id=12345,
        longitude=104.9282,
        latitude=11.5564,
        heartbeat_at=1794460000.0,
        event=sample,
    )

    assert result is True
    assert [call[0] for call in redis.calls] == [
        "geoadd", "zadd", "zremrangebyscore", "publish", "publish"
    ]
    assert redis.calls[-1][1][0] == "fleet:locations"


@pytest.mark.parametrize(
    "failed_operation",
    ["geoadd", "zadd", "zremrangebyscore", "publish"],
)
@pytest.mark.asyncio
async def test_redis_failure_is_reported_as_degraded_projection_not_exception(
    sample, failed_operation
):
    redis = FakeRedis(fail_on=failed_operation)

    result = await publish_driver_location(
        redis,
        telegram_id=12345,
        longitude=104.9282,
        latitude=11.5564,
        heartbeat_at=1794460000.0,
        event=sample,
    )

    assert result is False


@pytest.mark.asyncio
async def test_real_redis_projection_updates_geo_presence_and_delivers_pubsub(sample):
    """Exercise the production Redis client against the CI Redis service."""
    import asyncio
    import json
    import os
    import uuid

    from redis.asyncio import Redis

    redis = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    telegram_id = int(f"8{uuid.uuid4().int % 900000000}")
    channel = f"driver:location:{telegram_id}"
    pubsub = redis.pubsub()
    try:
        await pubsub.subscribe(channel)
        # Consume the subscription acknowledgement before publishing.
        await asyncio.wait_for(pubsub.get_message(ignore_subscribe_messages=False, timeout=1), 2)
        result = await publish_driver_location(
            redis,
            telegram_id=telegram_id,
            longitude=104.9282,
            latitude=11.5564,
            heartbeat_at=1794460000.0,
            event={**sample, "telegram_id": telegram_id},
        )
        assert result is True

        message = None
        async def receive():
            nonlocal message
            while message is None:
                candidate = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
                if candidate and candidate.get("type") == "message":
                    message = candidate
        await asyncio.wait_for(receive(), 5)
        assert json.loads(message["data"])["telegram_id"] == telegram_id
        assert await redis.zscore("driver:heartbeat", str(telegram_id)) == 1794460000.0
        assert await redis.geopos("driver:online", str(telegram_id)) is not None
    finally:
        await pubsub.aclose()
        await redis.zrem("driver:heartbeat", str(telegram_id))
        await redis.zrem("driver:online", str(telegram_id))
        await redis.aclose()
