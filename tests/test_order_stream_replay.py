import json

import pytest

from app.modules.orders.stream import load_order_stream_bootstrap


class FakeConnection:
    def __init__(self, *, status="IN_PROGRESS", latest=4, events=None, count=None):
        self.status = status
        self.latest = latest
        self.events = events or []
        self.count = len(self.events) if count is None else count
        self.calls = []

    async def fetchval(self, query, *args):
        self.calls.append((query, args))
        if "SELECT status FROM orders" in query:
            return self.status
        if "MAX(event_id)" in query:
            return self.latest
        if "COUNT(*)" in query:
            return self.count
        raise AssertionError(query)

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        assert "ORDER BY event_id" in query
        return self.events


@pytest.mark.asyncio
async def test_first_connection_receives_current_snapshot():
    connection = FakeConnection(status="ACCEPTED", latest=12)
    result = await load_order_stream_bootstrap(
        connection, order_id=8, last_event_id=0
    )
    assert result == {
        "type": "snapshot",
        "order_id": 8,
        "status": "ACCEPTED",
        "last_event_id": 12,
        "resync_required": False,
    }


@pytest.mark.asyncio
async def test_reconnect_replays_events_after_client_cursor_in_order():
    connection = FakeConnection(
        latest=14,
        events=[
            {"payload": {"event_id": 13, "status": "ARRIVED"}},
            {"payload": json.dumps({"event_id": 14, "status": "IN_PROGRESS"})},
        ],
    )
    result = await load_order_stream_bootstrap(
        connection, order_id=8, last_event_id=12
    )
    assert result["type"] == "replay"
    assert [event["event_id"] for event in result["events"]] == [13, 14]
    assert result["last_event_id"] == 14


@pytest.mark.asyncio
async def test_large_gap_requests_snapshot_instead_of_unbounded_replay():
    connection = FakeConnection(latest=1000, count=501)
    result = await load_order_stream_bootstrap(
        connection, order_id=8, last_event_id=1
    )
    assert result["type"] == "snapshot"
    assert result["resync_required"] is True
    assert result["last_event_id"] == 1000


@pytest.mark.asyncio
async def test_future_cursor_resets_to_current_snapshot():
    connection = FakeConnection(latest=14)
    result = await load_order_stream_bootstrap(
        connection, order_id=8, last_event_id=99
    )
    assert result["type"] == "snapshot"
    assert result["resync_required"] is True
    assert result["last_event_id"] == 14
