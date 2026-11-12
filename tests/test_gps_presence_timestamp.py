from datetime import datetime, timezone
import time

import pytest


@pytest.mark.asyncio
async def test_presence_heartbeat_is_stamped_after_serialized_database_transaction(monkeypatch):
    from app.modules.locations import router as locations_router

    state = {"transaction_finished_at": None, "heartbeat_at": None}

    class Connection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            state["transaction_finished_at"] = time.time()
            return False

        async def fetchrow(self, query, *args):
            if "SELECT id FROM users" in query:
                return {"id": 7}
            if "INSERT INTO driver_locations" in query:
                return {"driver_id": 7}
            raise AssertionError(f"Unexpected fetchrow query: {query}")

        async def fetchval(self, query, *args):
            if "SELECT recorded_at FROM driver_locations" in query:
                return None
            if "SELECT id FROM orders" in query:
                return None
            raise AssertionError(f"Unexpected fetchval query: {query}")

        async def execute(self, query, *args):
            raise AssertionError("No active trip should be updated")

        def transaction(self):
            return self

    class Pool:
        def acquire(self):
            return Connection()

    async def publish(_redis, **kwargs):
        state["heartbeat_at"] = kwargs["heartbeat_at"]
        assert state["transaction_finished_at"] is not None
        return True

    monkeypatch.setattr(locations_router.db, "pool", Pool())
    monkeypatch.setattr(locations_router.db, "redis", object())
    monkeypatch.setattr(locations_router, "publish_driver_location", publish)

    recorded_at = datetime.now(timezone.utc)
    result = await locations_router.update_driver_location(
        locations_router.LocationUpdate(
            latitude=11.5564,
            longitude=104.9282,
            recorded_at=recorded_at,
        ),
        claims={"sub": "12345"},
    )

    assert result["accepted"] is True
    assert result["realtime_published"] is True
    assert state["heartbeat_at"] >= state["transaction_finished_at"]
