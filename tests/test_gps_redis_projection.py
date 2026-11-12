import json
from datetime import datetime

import pytest

from app.modules.locations.redis_projection import publish_driver_location


class FakeRedis:
    def __init__(self, fail=False, result=1):
        self.fail = fail
        self.result = result
        self.calls = []

    async def eval(self, *args):
        self.calls.append(args)
        if self.fail:
            raise ConnectionError("simulated Redis outage")
        return self.result


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
        redis, telegram_id=12345, longitude=104.9282, latitude=11.5564,
        heartbeat_at=1794460000.0, event=sample,
    )
    assert result is True
    assert len(redis.calls) == 1
    script, key_count, *values = redis.calls[0]
    assert "ZSCORE" in script and "GEOADD" in script
    assert "ZREMRANGEBYSCORE" in script and script.count("PUBLISH") == 2
    assert key_count == 5
    assert values[:5] == [
        "driver:online", "driver:heartbeat", "driver:location:version",
        "driver:location:12345", "fleet:locations",
    ]
    assert values[5:8] == [104.9282, 11.5564, "12345"]
    assert values[8] == datetime.fromisoformat(sample["recorded_at"]).timestamp()
    assert values[9] == 1794460000.0
    assert json.loads(values[10]) == sample


@pytest.mark.asyncio
async def test_stale_projection_result_is_reported_as_not_published(sample):
    redis = FakeRedis(result=0)
    result = await publish_driver_location(
        redis, telegram_id=12345, longitude=104.9282, latitude=11.5564,
        heartbeat_at=1794460000.0, event=sample,
    )
    assert result is False
    assert len(redis.calls) == 1


@pytest.mark.asyncio
async def test_redis_failure_is_reported_as_degraded_projection_not_exception(sample):
    redis = FakeRedis(fail=True)
    result = await publish_driver_location(
        redis, telegram_id=12345, longitude=104.9282, latitude=11.5564,
        heartbeat_at=1794460000.0, event=sample,
    )
    assert result is False
    assert len(redis.calls) == 1


@pytest.mark.asyncio
async def test_real_redis_projection_rejects_late_older_samples(sample):
    """A late older sample cannot overwrite newer GPS state or emit a frame."""
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
        await asyncio.wait_for(pubsub.get_message(ignore_subscribe_messages=False, timeout=1), 2)
        newer = {
            **sample, "telegram_id": telegram_id, "latitude": 11.557,
            "recorded_at": "2026-11-12T05:00:02+00:00",
        }
        older = {
            **sample, "telegram_id": telegram_id, "latitude": 11.555,
            "recorded_at": "2026-11-12T05:00:01+00:00",
        }
        assert await publish_driver_location(
            redis, telegram_id=telegram_id, longitude=104.9282,
            latitude=11.557, heartbeat_at=1794460002.0, event=newer,
        ) is True
        assert await publish_driver_location(
            redis, telegram_id=telegram_id, longitude=104.9282,
            latitude=11.555, heartbeat_at=1794460003.0, event=older,
        ) is False
        message = await asyncio.wait_for(
            pubsub.get_message(ignore_subscribe_messages=True, timeout=1), 2
        )
        assert json.loads(message["data"])["latitude"] == 11.557
        assert await redis.zscore("driver:heartbeat", str(telegram_id)) == 1794460002.0
        assert await redis.zscore("driver:location:version", str(telegram_id)) == pytest.approx(
            datetime.fromisoformat(newer["recorded_at"]).timestamp()
        )
        position = await redis.geopos("driver:online", str(telegram_id))
        assert position[0] is not None
        assert position[0][1] == pytest.approx(11.557, abs=0.00001)
        assert await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.05) is None
    finally:
        await pubsub.aclose()
        await redis.zrem("driver:heartbeat", str(telegram_id))
        await redis.zrem("driver:location:version", str(telegram_id))
        await redis.zrem("driver:online", str(telegram_id))
        await redis.aclose()


@pytest.mark.asyncio
async def test_real_redis_client_recovers_after_connection_pool_disconnect(sample):
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
        await redis.zadd("driver:location:version", {telegram_id: 1794460000.0})
        monkeypatch.setattr(db, "redis", redis)
        nearby = await DispatchService.get_nearby_drivers(104.9282, 11.5564)
        assert telegram_id not in {str(item[0]) for item in nearby}
        assert await redis.zscore("driver:heartbeat", telegram_id) is None
        assert await redis.zscore("driver:location:version", telegram_id) is None
        assert await redis.geopos("driver:online", telegram_id) == [None]
    finally:
        await redis.zrem("driver:heartbeat", telegram_id)
        await redis.zrem("driver:location:version", telegram_id)
        await redis.zrem("driver:online", telegram_id)
        await redis.aclose()


@pytest.mark.asyncio
async def test_dispatch_expiry_cleanup_does_not_delete_a_concurrent_gps_heartbeat(monkeypatch):
    """A fresh GPS update between the expiry scan and cleanup must survive."""
    import os
    import time
    import uuid

    from redis.asyncio import Redis

    from app.db.core import db
    from app.services.dispatch import DispatchService

    redis = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    telegram_id = f"7{uuid.uuid4().int % 900000000}"
    fresh_heartbeat = time.time()
    fresh_version = fresh_heartbeat - 1

    class RacingRedis:
        async def zrangebyscore(self, *args, **kwargs):
            # Capture the stale member first, then simulate a GPS publisher
            # completing before DispatchService starts its cleanup operation.
            expired = await redis.zrangebyscore(*args, **kwargs)
            await redis.geoadd("driver:online", (104.9282, 11.5564, telegram_id))
            await redis.zadd("driver:heartbeat", {telegram_id: fresh_heartbeat})
            await redis.zadd("driver:location:version", {telegram_id: fresh_version})
            return expired

        async def eval(self, *args):
            return await redis.eval(*args)

        async def geosearch(self, *args, **kwargs):
            return await redis.geosearch(*args, **kwargs)

    try:
        await redis.geoadd("driver:online", (104.9282, 11.5564, telegram_id))
        await redis.zadd("driver:heartbeat", {telegram_id: fresh_heartbeat - 600})
        await redis.zadd("driver:location:version", {telegram_id: fresh_version - 600})
        monkeypatch.setattr(db, "redis", RacingRedis())

        nearby = await DispatchService.get_nearby_drivers(104.9282, 11.5564)

        assert telegram_id in {str(item[0]) for item in nearby}
        assert await redis.zscore("driver:heartbeat", telegram_id) == pytest.approx(fresh_heartbeat)
        assert await redis.zscore("driver:location:version", telegram_id) == pytest.approx(fresh_version)
        position = (await redis.geopos("driver:online", telegram_id))[0]
        assert position is not None
        assert position[0] == pytest.approx(104.9282, abs=0.0001)
        assert position[1] == pytest.approx(11.5564, abs=0.0001)
    finally:
        await redis.zrem("driver:heartbeat", telegram_id)
        await redis.zrem("driver:location:version", telegram_id)
        await redis.zrem("driver:online", telegram_id)
        await redis.aclose()


@pytest.mark.asyncio
async def test_driver_online_and_offline_transitions_update_presence_atomically(monkeypatch):
    import os
    import uuid

    from redis.asyncio import Redis

    from app.db.core import db
    from app.services.dispatch import DispatchService

    redis = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    telegram_id = f"6{uuid.uuid4().int % 900000000}"
    try:
        monkeypatch.setattr(db, "redis", redis)
        await DispatchService.go_online(telegram_id, 104.9282, 11.5564)

        assert await redis.zscore("driver:heartbeat", telegram_id) is not None
        position = (await redis.geopos("driver:online", telegram_id))[0]
        assert position is not None
        assert position[0] == pytest.approx(104.9282, abs=0.0001)
        assert position[1] == pytest.approx(11.5564, abs=0.0001)

        await redis.zadd("driver:location:version", {telegram_id: 123.0})
        await DispatchService.go_offline(telegram_id)

        assert await redis.zscore("driver:heartbeat", telegram_id) is None
        assert await redis.zscore("driver:location:version", telegram_id) is None
        assert await redis.geopos("driver:online", telegram_id) == [None]
    finally:
        await redis.zrem("driver:heartbeat", telegram_id)
        await redis.zrem("driver:location:version", telegram_id)
        await redis.zrem("driver:online", telegram_id)
        await redis.aclose()


@pytest.mark.asyncio
async def test_dispatch_expiry_cleanup_bounds_redis_script_batches(monkeypatch):
    import os
    import time
    import uuid

    from redis.asyncio import Redis

    from app.db.core import db
    from app.services.dispatch import DispatchService

    redis = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    prefix = f"bounded-stale-{uuid.uuid4().hex}-"
    members = [f"{prefix}{index}" for index in range(1200)]
    stale_score = time.time() - 600
    try:
        await redis.zadd("driver:heartbeat", {member: stale_score for member in members})
        await redis.zadd("driver:location:version", {member: stale_score for member in members})
        await redis.geoadd(
            "driver:online",
            [(104.9282 + index * 0.000001, 11.5564, member)
             for index, member in enumerate(members)],
        )

        class CountingRedis:
            def __init__(self):
                self.script_sizes = []

            async def zrangebyscore(self, *args, **kwargs):
                return await redis.zrangebyscore(*args, **kwargs)

            async def eval(self, script, key_count, *args):
                self.script_sizes.append(len(args) - 3)
                return await redis.eval(script, key_count, *args)

            async def geosearch(self, *args, **kwargs):
                return await redis.geosearch(*args, **kwargs)

        counted = CountingRedis()
        monkeypatch.setattr(db, "redis", counted)
        await DispatchService.get_nearby_drivers(104.9282, 11.5564)

        assert counted.script_sizes == [500, 500, 200]
        assert await redis.zscore("driver:heartbeat", members[0]) is None
        assert await redis.zscore("driver:heartbeat", members[-1]) is None
        assert await redis.geopos("driver:online", members[0]) == [None]
    finally:
        await redis.zrem("driver:heartbeat", *members)
        await redis.zrem("driver:location:version", *members)
        await redis.zrem("driver:online", *members)
        await redis.aclose()
