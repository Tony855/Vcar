import pytest

from app.db.core import db
from app.services import driver_presence_projection as projection


class FakeConnection:
    pass


class Acquire:
    async def __aenter__(self):
        return FakeConnection()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class Pool:
    def acquire(self):
        return Acquire()


class StopAfterCycles:
    def __init__(self, cycles):
        self.cycles = cycles
        self.waits = 0

    def is_set(self):
        return self.waits >= self.cycles

    async def wait(self):
        self.waits += 1
        return True


@pytest.mark.asyncio
async def test_presence_worker_recovers_after_transient_redis_failure(monkeypatch):
    stop = StopAfterCycles(2)
    calls = []

    async def reconcile(redis_client, connection):
        calls.append((redis_client, connection))
        if len(calls) == 1:
            raise ConnectionError("simulated Redis outage")
        return {"database_fresh": 500, "repaired": 498, "stale_removed": 2}

    monkeypatch.setattr(db, "pool", Pool())
    redis_client = object()
    monkeypatch.setattr(db, "redis", redis_client)
    monkeypatch.setattr(projection, "reconcile_driver_presence", reconcile)
    for key, value in {
        "last_success_timestamp": 0.0,
        "last_failure_timestamp": 0.0,
        "consecutive_failures": 0,
        "last_duration_seconds": 0.0,
        "database_fresh": 0,
        "repaired": 0,
        "stale_removed": 0,
    }.items():
        monkeypatch.setitem(projection.PRESENCE_RECONCILIATION_STATE, key, value)

    await projection.run_driver_presence_reconciliation_worker(stop, interval=1)

    state = projection.PRESENCE_RECONCILIATION_STATE
    assert len(calls) == 2
    assert calls[0][0] is redis_client
    assert calls[1][0] is redis_client
    assert state["last_failure_timestamp"] > 0
    assert state["last_success_timestamp"] > 0
    assert state["consecutive_failures"] == 0
    assert state["last_duration_seconds"] >= 0
    assert state["database_fresh"] == 500
    assert state["repaired"] == 498
    assert state["stale_removed"] == 2


@pytest.mark.asyncio
async def test_presence_worker_rejects_too_short_interval():
    with pytest.raises(ValueError, match="at least one second"):
        await projection.run_driver_presence_reconciliation_worker(
            StopAfterCycles(0), interval=0.5
        )
