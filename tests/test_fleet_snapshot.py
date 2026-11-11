from datetime import datetime, timedelta, timezone

import pytest

from app.modules.locations.fleet import build_fleet_snapshot, fetch_fleet_snapshot


NOW = datetime(2026, 11, 12, 5, 0, tzinfo=timezone.utc)


def row(telegram_id, received_at, **extra):
    return {
        "id": telegram_id,
        "telegram_id": telegram_id,
        "latitude": 11.5564,
        "longitude": 104.9282,
        "received_at": received_at,
        **extra,
    }


def test_fleet_snapshot_marks_recent_driver_online():
    snapshot = build_fleet_snapshot(
        [row(1001, NOW - timedelta(seconds=30))],
        now=NOW,
    )
    assert snapshot[0]["online"] is True


def test_fleet_snapshot_marks_stale_driver_offline():
    snapshot = build_fleet_snapshot(
        [row(1001, NOW - timedelta(seconds=121))],
        now=NOW,
    )
    assert snapshot[0]["online"] is False


@pytest.mark.parametrize(
    "received_at",
    [
        None,
        datetime(2026, 11, 12, 4, 59),  # Naive timestamps fail closed.
        NOW + timedelta(seconds=1),    # Future receipt timestamps fail closed.
    ],
)
def test_fleet_snapshot_fails_closed_for_missing_or_invalid_timestamp(received_at):
    snapshot = build_fleet_snapshot([row(1001, received_at)], now=NOW)
    assert snapshot[0]["online"] is False


def test_fleet_snapshot_preserves_driver_metadata_and_empty_state():
    driver = row(1001, NOW, first_name="Driver", plate_number="A-123")
    snapshot = build_fleet_snapshot([driver], now=NOW)
    assert snapshot == [{**driver, "online": True}]
    assert build_fleet_snapshot([], now=NOW) == []


@pytest.mark.asyncio
async def test_fetch_fleet_snapshot_reads_durable_approved_fleet():
    class Connection:
        async def fetch(self, query):
            assert "driver_status='APPROVED'" in query
            return [row(1001, NOW)]

    snapshot = await fetch_fleet_snapshot(Connection(), now=NOW)
    assert len(snapshot) == 1
    assert snapshot[0]["telegram_id"] == 1001
    assert snapshot[0]["online"] is True


@pytest.mark.asyncio
async def test_admin_fleet_stream_subscribes_before_sending_durable_snapshot(monkeypatch):
    from fastapi import WebSocketDisconnect

    from app.modules.locations import router as locations_router

    order = []
    driver = row(1001, NOW, first_name="Driver")

    class Connection:
        async def fetch(self, query):
            order.append("database_snapshot")
            assert "driver_status='APPROVED'" in query
            return [driver]

    class Acquire:
        async def __aenter__(self):
            return Connection()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class Pool:
        def acquire(self):
            return Acquire()

    class PubSub:
        async def subscribe(self, channel):
            order.append("subscribe")
            assert channel == "fleet:locations"

        async def get_message(self, **kwargs):
            raise WebSocketDisconnect(code=1000)

        async def unsubscribe(self, channel):
            order.append("unsubscribe")

        async def aclose(self):
            order.append("close")

    class Redis:
        def pubsub(self):
            return PubSub()

    class WebSocket:
        sent = None

        async def accept(self):
            order.append("accept")

        async def send_json(self, payload):
            self.sent = payload
            order.append("send_snapshot")

    async def valid_ticket(ticket, scope):
        assert ticket == "single-use"
        assert scope == "admin-fleet"
        return {"role": "admin", "sub": "admin-1"}

    websocket = WebSocket()
    monkeypatch.setattr(locations_router, "consume_ws_ticket", valid_ticket)
    monkeypatch.setattr(locations_router.db, "pool", Pool())
    monkeypatch.setattr(locations_router.db, "redis", Redis())

    await locations_router.admin_fleet_stream(websocket, "single-use")

    assert order.index("subscribe") < order.index("database_snapshot")
    assert websocket.sent["type"] == "snapshot"
    assert websocket.sent["drivers"][0]["telegram_id"] == 1001
    assert websocket.sent["drivers"][0]["online"] is True
