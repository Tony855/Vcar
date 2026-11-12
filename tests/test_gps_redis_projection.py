import json

import pytest

from app.modules.locations.redis_projection import publish_driver_location


class FakeRedis:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    async def eval(self, *args):
        self.calls.append(args)
        if self.fail:
            raise ConnectionError("simulated Redis outage")
        return 1


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
async def test_projection_uses_one_atomic_script_for_presence_and_both_streams(sample):
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
    assert len(redis.calls) == 1
    script, key_count, *values = redis.calls[0]
    assert "ZSCORE" in script and "GEOADD" in script
    assert "ZREMRANGEBYSCORE" in script and script.count("PUBLISH") == 2
    assert key_count == 4
    assert values[:4] == [
        "driver:online",
        "driver:heartbeat",
        "driver:location:12345",
        "fleet:locations",
    ]
    assert values[4:8] == [104.9282, 11.5564, "12345", 1794460000.0]
    assert json.loads(values[8]) == sample


@pytest.mark.asyncio
async def test_redis_failure_is_reported_as_degraded_projection_not_exception(sample):
    redis = FakeRedis(fail=True)

    result = await publish_driver_location(
        redis,
        telegram_id=12345,
        longitude=104.9282,
        latitude=11.5564,
        heartbeat_at=1794460000.0,
        event=sample,
    )

    assert result is False
    assert len(redis.calls) == 1


@pytest.mark.asyncio
async def test_real_redis_projection_is_atomic_and_rejects_late_older_updates(sample):
    """An older request completing late must not overwrite newer Redis state."""
    import asyncio
    import os
    import uuid

    from redis.asyncio import Redis

    redis = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    telegram_id = int(f"8{uuid.uuid4().int % 900000000}")
    channel = f"driver:location:{telegram_id}"
    pubsub = redis.pubsub()
    try:
        await pubsub.subscribe(channel)
        await asyncio.wait_for(
            pubsub.get_message(ignore_subscribe_messages=False, timeout=1), 2
        )

        newer = {**sample, "telegram_id": telegram_id, "latitude": 11.557}
        older = {**sample, "telegram_id": telegram_id, "latitude": 11.555}
        assert await publish_driver_location(
            redis, telegram_id=telegram_id, longitude=104.9282,
            latitude=11.557, heartbeat_at=1794460002.0, event=newer,
        )
        assert await publish_driver_location(
            redis, telegram_id=telegram_id, longitude=104.9282,
            latitude=11.555, heartbeat_at=1794460001.0, event=older,
        )

        message = await asyncio.wait_for(
            pubsub.get_message(ignore_subscribe_messages=True, timeout=1), 2
        )
        assert json.loads(message["data"])["latitude"] == 11.557
        assert await redis.zscore("driver:heartbeat", str(telegram_id)) == 1794460002.0
        position = await redis.geopos("driver:online", str(telegram_id))
        assert position[0] is not None
        assert position[0][1] == pytest.approx(11.557, abs=0.00001)

        # No second Pub/Sub frame should be emitted for the stale request.
        assert await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.05) is None
    finally:
        await pubsub.aclose()
        await redis.zrem("driver:heartbeat", str(telegram_id))
        await redis.zrem("driver:online", str(telegram_id))
        await redis.aclose()


@pytest.mark.asyncio
async def test_real_redis_client_recovers_after_connection_pool_disconnect(sample):
    """A dropped Redis socket must be re-established by the production client."""
    import os
    import uuid

    from redis.asyncio import Redis

    redis = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    key = f"vcar:redis-reconnect:{uuid.uuid4().hex}"
    try:
        assert await redis.ping() is True
        await redis.set(key, "before-disconnect")
        await redis.connection_pool.disconnect(inuse_connections=True)
        assert await redis.ping() is True
        assert await redis.get(key) == "before-disconnect"
        await redis.set(key, "after-disconnect")
        assert await redis.get(key) == "after-disconnect"
    finally:
        await redis.delete(key)
        await redis.aclose()


@pytest.mark.asyncio
async def test_real_redis_dispatch_removes_expired_driver_from_geo_index(monkeypatch):
    """Expired heartbeats must remove both presence and the GEO candidate."""
    import os
    import time
    import uuid

    from redis.asyncio import Redis

    from app.db.core import db
    from app.services.dispatch import DispatchService

    redis = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    telegram_id = f"9{uuid.uuid4().int % 900000000}"
    try:
        await redis.geoadd("driver:online", (104.9282, 11.5564, telegram_id))
        await redis.zadd("driver:heartbeat", {telegram_id: time.time() - 600})
        monkeypatch.setattr(db, "redis", redis)

        nearby = await DispatchService.get_nearby_drivers(104.9282, 11.5564)
        assert telegram_id not in {str(item[0]) for item in nearby}
        assert await redis.zscore("driver:heartbeat", telegram_id) is None
        assert await redis.geopos("driver:online", telegram_id) == [None]
    finally:
        await redis.zrem("driver:heartbeat", telegram_id)
        await redis.zrem("driver:online", telegram_id)
        await redis.aclose()
