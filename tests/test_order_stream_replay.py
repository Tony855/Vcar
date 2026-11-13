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

    async def fetchrow(self, query, *args):
        self.calls.append((query, args))
        assert "SELECT o.status" in query
        return {"status": self.status, "latest_event_id": self.latest}

    async def fetchval(self, query, *args):
        self.calls.append((query, args))
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
            {"event_id": 13, "payload": {"event_id": 999, "order_id": 777, "status": "ARRIVED"}},
            {"event_id": 14, "payload": json.dumps({"event_id": 998, "order_id": 776, "status": "IN_PROGRESS"})},
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


@pytest.mark.asyncio
async def test_passenger_stream_replays_and_deduplicates_pubsub_overlap(monkeypatch):
    from fastapi import WebSocketDisconnect

    from app.modules.passengers import router as passenger_router

    order = []

    class Connection:
        async def fetchval(self, query, *args):
            if "SELECT id FROM orders" in query:
                return 8
            if "COUNT(*)" in query:
                return 2
            raise AssertionError(query)

        async def fetchrow(self, query, *args):
            order.append("database_snapshot")
            assert "SELECT o.status" in query
            return {"status": "IN_PROGRESS", "latest_event_id": 5}

        async def fetch(self, query, *args):
            assert "ORDER BY event_id" in query
            return [
                {"event_id": 4, "payload": {"event_id": 404, "order_id": 999, "status": "ARRIVED"}},
                {"event_id": 5, "payload": {"event_id": 505, "order_id": 999, "status": "IN_PROGRESS"}},
            ]

    class Acquire:
        async def __aenter__(self):
            return Connection()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class Pool:
        def acquire(self):
            return Acquire()

    class PubSub:
        def __init__(self):
            self.frames = [
                {"type": "message", "data": json.dumps(["malformed", "json", "shape"])},
                {"type": "message", "data": json.dumps({"event_id": 6, "order_id": 999, "status": "ARRIVED"})},
                {"type": "message", "data": json.dumps({"event_id": 5, "order_id": 8, "status": "IN_PROGRESS"})},
                {"type": "message", "data": json.dumps({"event_id": 6, "order_id": 8, "status": "ARRIVED"})},
                # At-least-once outbox delivery may publish the same event again.
                {"type": "message", "data": json.dumps({"event_id": 6, "order_id": 8, "status": "ARRIVED"})},
                {"type": "message", "data": json.dumps({"event_id": 7, "order_id": 8, "status": "COMPLETED"})},
            ]

        async def subscribe(self, channel):
            order.append("subscribe")

        async def get_message(self, **kwargs):
            if self.frames:
                return self.frames.pop(0)
            raise WebSocketDisconnect(code=1000)

        async def unsubscribe(self, channel):
            pass

        async def aclose(self):
            pass

    class Redis:
        def pubsub(self):
            return PubSub()

    class WebSocket:
        def __init__(self):
            self.sent = []

        async def accept(self):
            order.append("accept")

        async def close(self, code):
            self.close_code = code

        async def send_json(self, payload):
            self.sent.append(payload)

    async def valid_ticket(ticket, scope):
        assert scope == "passenger-order:8"
        return {"sub": "1001", "role": "passenger"}

    websocket = WebSocket()
    monkeypatch.setattr(passenger_router, "consume_ws_ticket", valid_ticket)
    monkeypatch.setattr(passenger_router.db, "pool", Pool())
    monkeypatch.setattr(passenger_router.db, "redis", Redis())

    await passenger_router.passenger_order_stream(
        websocket, 8, "single-use-ticket", last_event_id=3
    )

    assert order.index("subscribe") < order.index("database_snapshot")
    assert [event["event_id"] for event in websocket.sent] == [4, 5, 6, 7]
    assert websocket.sent[-1]["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_passenger_stream_uses_durable_outbox_when_redis_is_unavailable_at_connect(monkeypatch):
    from fastapi import WebSocketDisconnect

    from app.modules.passengers import router as passenger_router

    bootstrap_calls = []

    class Connection:
        async def fetchval(self, query, *args):
            if "SELECT id FROM orders" in query:
                return 8
            if "COUNT(*)" in query:
                return 1
            raise AssertionError(query)

        async def fetchrow(self, query, *args):
            bootstrap_calls.append("bootstrap")
            if len(bootstrap_calls) == 1:
                return {"status": "IN_PROGRESS", "latest_event_id": 3}
            return {"status": "COMPLETED", "latest_event_id": 4}

        async def fetch(self, query, *args):
            return [{"event_id": 4, "payload": {
                "event_id": 404, "order_id": 999, "status": "COMPLETED"
            }}]

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
            raise RuntimeError("Redis unavailable")

        async def unsubscribe(self, channel):
            pass

        async def aclose(self):
            pass

    class RecoveredPubSub:
        async def subscribe(self, channel):
            pass

        async def get_message(self, **kwargs):
            raise WebSocketDisconnect(code=1000)

        async def unsubscribe(self, channel):
            pass

        async def aclose(self):
            pass

    class Redis:
        def __init__(self):
            self.calls = 0

        def pubsub(self):
            self.calls += 1
            return PubSub() if self.calls == 1 else RecoveredPubSub()

    class WebSocket:
        def __init__(self):
            self.sent = []

        async def accept(self):
            pass

        async def close(self, code):
            self.close_code = code

        async def send_json(self, payload):
            self.sent.append(payload)

    async def valid_ticket(ticket, scope):
        return {"sub": "1001", "role": "passenger"}

    async def no_wait(_seconds):
        pass

    websocket = WebSocket()
    redis = Redis()
    monkeypatch.setattr(passenger_router, "consume_ws_ticket", valid_ticket)
    monkeypatch.setattr(passenger_router.db, "pool", Pool())
    monkeypatch.setattr(passenger_router.db, "redis", redis)
    monkeypatch.setattr(passenger_router.asyncio, "sleep", no_wait)

    await passenger_router.passenger_order_stream(
        websocket, 8, "single-use-ticket", last_event_id=0
    )

    assert redis.calls == 2
    assert [frame["type"] if "type" in frame else frame["status"] for frame in websocket.sent] == [
        "snapshot", "COMPLETED"
    ]
    assert websocket.sent[-1]["event_id"] == 4


@pytest.mark.asyncio
async def test_passenger_stream_recovers_committed_events_after_pubsub_disconnect(monkeypatch):
    from fastapi import WebSocketDisconnect

    from app.modules.passengers import router as passenger_router

    bootstrap_calls = []

    class Connection:
        async def fetchval(self, query, *args):
            if "SELECT id FROM orders" in query:
                return 8
            if "COUNT(*)" in query:
                return 1
            raise AssertionError(query)

        async def fetchrow(self, query, *args):
            bootstrap_calls.append("bootstrap")
            if len(bootstrap_calls) == 1:
                return {"status": "IN_PROGRESS", "latest_event_id": 5}
            return {"status": "COMPLETED", "latest_event_id": 6}

        async def fetch(self, query, *args):
            return [{"event_id": 6, "payload": {
                "event_id": 606, "order_id": 999, "status": "COMPLETED"
            }}]

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
            pass

        async def get_message(self, **kwargs):
            raise RuntimeError("Redis connection dropped")

        async def unsubscribe(self, channel):
            pass

        async def aclose(self):
            pass

    class Redis:
        def pubsub(self):
            return PubSub()

    class WebSocket:
        def __init__(self):
            self.sent = []

        async def accept(self):
            pass

        async def close(self, code):
            self.close_code = code

        async def send_json(self, payload):
            self.sent.append(payload)

    async def valid_ticket(ticket, scope):
        return {"sub": "1001", "role": "passenger"}

    websocket = WebSocket()
    monkeypatch.setattr(passenger_router, "consume_ws_ticket", valid_ticket)
    monkeypatch.setattr(passenger_router.db, "pool", Pool())
    monkeypatch.setattr(passenger_router.db, "redis", Redis())

    await passenger_router.passenger_order_stream(
        websocket, 8, "single-use-ticket", last_event_id=0
    )

    assert len(bootstrap_calls) == 2
    assert websocket.sent[0]["type"] == "snapshot"
    assert websocket.sent[-1]["event_id"] == 6
    assert websocket.sent[-1]["status"] == "COMPLETED"
