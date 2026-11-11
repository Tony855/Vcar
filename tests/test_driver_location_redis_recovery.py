from datetime import datetime, timezone

import pytest


@pytest.mark.asyncio
async def test_driver_location_stream_rebuilds_redis_subscription_and_sends_snapshot(monkeypatch):
    from fastapi import WebSocketDisconnect

    from app.modules.locations import router as locations_router

    calls = {"pubsubs": 0, "subscriptions": 0, "reads": 0, "messages": 0}
    current = datetime.now(timezone.utc)

    class Connection:
        async def fetchrow(self, query, telegram_id):
            calls["reads"] += 1
            return {
                "latitude": 11.5564,
                "longitude": 104.9282,
                "accuracy_m": 5.0,
                "heading_deg": 90.0,
                "speed_mps": 3.0,
                "recorded_at": current,
            }

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
            calls["subscriptions"] += 1

        async def get_message(self, **kwargs):
            calls["messages"] += 1
            if calls["messages"] == 1:
                raise ConnectionError("redis restarted")
            raise WebSocketDisconnect(code=1000)

        async def unsubscribe(self, channel):
            pass

        async def aclose(self):
            pass

    class Redis:
        def pubsub(self):
            calls["pubsubs"] += 1
            return PubSub()

    class WebSocket:
        def __init__(self):
            self.sent = []

        async def accept(self):
            pass

        async def send_json(self, payload):
            self.sent.append(payload)

        async def send_text(self, payload):
            raise AssertionError("No live event expected in this test")

    async def valid_ticket(ticket, scope):
        assert scope == "driver-location"
        return {"role": "driver", "sub": "12345"}

    async def no_sleep(*_args):
        return None

    websocket = WebSocket()
    monkeypatch.setattr(locations_router, "consume_ws_ticket", valid_ticket)
    monkeypatch.setattr(locations_router.db, "pool", Pool())
    monkeypatch.setattr(locations_router.db, "redis", Redis())
    monkeypatch.setattr(locations_router.asyncio, "sleep", no_sleep)

    await locations_router.driver_location_stream(websocket, "single-use")

    assert len(websocket.sent) == 2
    assert all(frame["snapshot"] is True for frame in websocket.sent)
    assert calls["pubsubs"] == 2
    assert calls["subscriptions"] == 2
    assert calls["reads"] == 2


@pytest.mark.asyncio
async def test_driver_location_stream_stays_available_when_redis_initial_subscribe_fails(monkeypatch):
    from fastapi import WebSocketDisconnect

    from app.modules.locations import router as locations_router

    calls = {"pubsubs": 0, "reads": 0, "subscriptions": 0}

    class Connection:
        async def fetchrow(self, query, telegram_id):
            calls["reads"] += 1
            return None

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
            calls["subscriptions"] += 1
            if calls["subscriptions"] == 1:
                raise ConnectionError("redis unavailable")

        async def get_message(self, **kwargs):
            raise WebSocketDisconnect(code=1000)

        async def aclose(self):
            pass

    class Redis:
        def pubsub(self):
            calls["pubsubs"] += 1
            return PubSub()

    class WebSocket:
        async def accept(self):
            pass

        async def send_json(self, payload):
            raise AssertionError("No accepted GPS snapshot exists")

        async def close(self, code):
            pass

    async def valid_ticket(ticket, scope):
        return {"role": "driver", "sub": "12345"}

    async def no_sleep(*_args):
        return None

    websocket = WebSocket()
    monkeypatch.setattr(locations_router, "consume_ws_ticket", valid_ticket)
    monkeypatch.setattr(locations_router.db, "pool", Pool())
    monkeypatch.setattr(locations_router.db, "redis", Redis())
    monkeypatch.setattr(locations_router.asyncio, "sleep", no_sleep)

    await locations_router.driver_location_stream(websocket, "single-use")

    assert calls["pubsubs"] == 2
    assert calls["subscriptions"] == 2
    assert calls["reads"] == 2
