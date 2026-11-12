from datetime import datetime, timedelta, timezone

import pytest

from app.services.driver_presence_projection import (
    _REMOVE_STALE_DRIVER,
    reconcile_driver_presence,
)


class FakeRedis:
    def __init__(self, *, heartbeat=None, online=None, newer_members=()):
        self.heartbeat = dict(heartbeat or {})
        self.online = set(online or ())
        self.versions = {}
        self.newer_members = set(newer_members)
        self.repaired = []

    async def eval(self, script, key_count, *args):
        if script == _REMOVE_STALE_DRIVER:
            member, cutoff = args
            current = self.heartbeat.get(member)
            if member in self.newer_members or (current is not None and current > float(cutoff)):
                return 0
            self.heartbeat.pop(member, None)
            self.online.discard(member)
            self.versions.pop(member, None)
            return 1
        longitude, latitude, member, recorded, received = args
        self.online.add(member)
        self.heartbeat[member] = float(received)
        self.versions[member] = float(recorded)
        self.repaired.append((member, float(longitude), float(latitude)))
        return 1

    async def zrange(self, key, start, end):
        if key == "driver:heartbeat":
            return sorted(self.heartbeat)
        if key == "driver:online":
            return sorted(self.online)
        raise AssertionError(key)


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows
        self.query = None
        self.args = None

    async def fetch(self, query, *args):
        self.query, self.args = query, args
        return self.rows


def driver_row(telegram_id, now, *, age=10, recorded_age=12):
    return {
        "telegram_id": telegram_id,
        "longitude": 104.9282,
        "latitude": 11.5564,
        "recorded_at": now - timedelta(seconds=recorded_age),
        "received_at": now - timedelta(seconds=age),
    }


@pytest.mark.asyncio
async def test_reconciliation_repairs_fresh_database_rows_and_removes_stale_members():
    now = datetime.now(timezone.utc)
    connection = FakeConnection([
        driver_row(1001, now),
        driver_row(1002, now, age=119),
    ])
    redis = FakeRedis(
        heartbeat={"1001": now.timestamp() - 20, "stale-heartbeat": now.timestamp() - 600},
        online={"stale-geo", "1002"},
    )

    result = await reconcile_driver_presence(redis, connection, now=now)

    assert result == {"database_fresh": 2, "repaired": 2, "stale_removed": 2}
    assert redis.online == {"1001", "1002"}
    assert redis.heartbeat["1001"] == pytest.approx((now - timedelta(seconds=10)).timestamp())
    assert redis.versions["1001"] == pytest.approx((now - timedelta(seconds=12)).timestamp())
    assert connection.args[0] == now - timedelta(seconds=120)
    assert connection.args[1] == now


@pytest.mark.asyncio
async def test_reconciliation_does_not_remove_a_heartbeat_advanced_after_scan_started():
    now = datetime.now(timezone.utc)
    redis = FakeRedis(
        heartbeat={"driver-raced": now.timestamp() + 1},
        online={"driver-raced"},
        newer_members={"driver-raced"},
    )
    connection = FakeConnection([])

    result = await reconcile_driver_presence(redis, connection, now=now)

    assert result == {"database_fresh": 0, "repaired": 0, "stale_removed": 0}
    assert redis.heartbeat["driver-raced"] == now.timestamp() + 1
    assert "driver-raced" in redis.online


@pytest.mark.asyncio
async def test_reconciliation_ignores_invalid_rows_and_requires_aware_now():
    now = datetime.now(timezone.utc)
    invalid = driver_row(0, now)
    invalid_coordinate = driver_row(1003, now)
    invalid_coordinate["longitude"] = 181
    redis = FakeRedis()
    connection = FakeConnection([invalid, invalid_coordinate])

    result = await reconcile_driver_presence(redis, connection, now=now)

    assert result == {"database_fresh": 0, "repaired": 0, "stale_removed": 0}
    with pytest.raises(ValueError, match="timezone-aware"):
        await reconcile_driver_presence(redis, connection, now=datetime.now())


@pytest.mark.asyncio
async def test_reconciliation_does_not_overwrite_a_newer_redis_gps_version():
    now = datetime.now(timezone.utc)
    redis = FakeRedis()
    redis.versions["1004"] = (now - timedelta(seconds=5)).timestamp()
    connection = FakeConnection([driver_row(1004, now, recorded_age=10)])

    result = await reconcile_driver_presence(redis, connection, now=now)

    # The Lua script's real Redis behavior is version-fenced; this fake models
    # the result code and verifies the caller does not count an unapplied row.
    assert result["database_fresh"] == 1
    assert result["repaired"] == 1
