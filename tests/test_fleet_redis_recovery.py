from datetime import datetime, timezone

import pytest

def row(telegram_id, received_at):
    return {"id": telegram_id, "telegram_id": telegram_id, "latitude": 11.5, "longitude": 104.9, "received_at": received_at}


@pytest.mark.asyncio
async def test_admin_fleet_stream_falls_back_to_database_snapshot_when_redis_fails(monkeypatch):
    from fastapi import WebSocketDisconnect

    from app.modules.locations import router as locations_router

    current = datetime.now(timezone.utc)
    driver = row(1002, current)
    calls = {"redis": 0, "pubsubs": 0, "subscriptions": 0}

    class Connection:
        async def fetch(self, query):
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
            calls["subscriptions"] += 1

        async def get_message(self, **kwargs):
            calls["redis"] += 1
            if calls["redis"] == 1:
                raise ConnectionError("redis unavailable")
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
        sent = []

        async def accept(self):
            pass

        async def send_json(self, payload):
            self.sent.append(payload)

    async def valid_ticket(ticket, scope):
        return {"role": "admin", "sub": "admin-1"}

    websocket = WebSocket()
    monkeypatch.setattr(locations_router, "consume_ws_ticket", valid_ticket)
    monkeypatch.setattr(locations_router.db, "pool", Pool())
    monkeypatch.setattr(locations_router.db, "redis", Redis())
    monkeypatch.setattr(locations_router.asyncio, "sleep", lambda *_: _noop())

    await locations_router.admin_fleet_stream(websocket, "single-use")

    assert len(websocket.sent) == 3
    assert websocket.sent[0]["type"] == "snapshot"
    assert websocket.sent[1]["reason"] == "redis_recovery"
    assert websocket.sent[2]["reason"] == "redis_recovery"
    assert websocket.sent[2]["drivers"][0]["telegram_id"] == 1002
    assert calls["pubsubs"] == 2
    assert calls["subscriptions"] == 2


async def _noop():
    return None
