from types import SimpleNamespace

import jwt
import pytest
from fastapi import HTTPException

from app.modules.passengers import router as passenger_router


class FakeConnection:
    def __init__(self, owned_order=None):
        self.owned_order = owned_order
        self.fetchval_calls = []

    async def fetchval(self, query, *args):
        self.fetchval_calls.append((query, args))
        return self.owned_order


class FakeAcquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakePool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return FakeAcquire(self.connection)


class FakeWebSocket:
    def __init__(self):
        self.accepted = False
        self.close_code = None

    async def close(self, code):
        self.close_code = code

    async def accept(self):
        self.accepted = True


@pytest.fixture
def jwt_secret(monkeypatch):
    secret = "passenger-test-secret-" + "x" * 40
    monkeypatch.setattr(passenger_router, "settings", SimpleNamespace(secret_key=secret))
    return secret


def make_token(secret, *, subject="1001", role="passenger", expires=300):
    import time

    return jwt.encode(
        {"sub": subject, "role": role, "exp": int(time.time()) + expires},
        secret,
        algorithm="HS256",
    )


def test_passenger_claims_requires_bearer_and_passenger_role(jwt_secret):
    with pytest.raises(HTTPException) as missing:
        passenger_router.passenger_claims(None)
    assert missing.value.status_code == 401

    admin_token = make_token(jwt_secret, role="admin")
    with pytest.raises(HTTPException) as forbidden:
        passenger_router.passenger_claims("Bearer " + admin_token)
    assert forbidden.value.status_code == 403

    passenger_token = make_token(jwt_secret)
    claims = passenger_router.passenger_claims("Bearer " + passenger_token)
    assert claims["sub"] == "1001"
    assert claims["role"] == "passenger"


def test_passenger_claims_rejects_expired_token(jwt_secret):
    expired = make_token(jwt_secret, expires=-1)
    with pytest.raises(HTTPException) as error:
        passenger_router.passenger_claims("Bearer " + expired)
    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_order_stream_rejects_invalid_ticket_before_database_access(monkeypatch):
    websocket = FakeWebSocket()
    connection = FakeConnection(owned_order=987)
    monkeypatch.setattr(passenger_router.db, "pool", FakePool(connection))

    async def invalid_ticket(ticket, scope):
        assert ticket == "bad-ticket"
        assert scope == "passenger-order:987"
        return None

    monkeypatch.setattr(passenger_router, "consume_ws_ticket", invalid_ticket)
    await passenger_router.passenger_order_stream(websocket, 987, "bad-ticket")

    assert websocket.close_code == 4401
    assert websocket.accepted is False
    assert connection.fetchval_calls == []


@pytest.mark.asyncio
async def test_order_stream_rejects_order_owned_by_another_passenger(monkeypatch):
    websocket = FakeWebSocket()
    connection = FakeConnection(owned_order=None)
    monkeypatch.setattr(passenger_router.db, "pool", FakePool(connection))

    async def valid_ticket(ticket, scope):
        assert ticket == "single-use-ticket"
        assert scope == "passenger-order:987"
        return {"sub": "1001", "role": "passenger", "scope": scope}

    monkeypatch.setattr(passenger_router, "consume_ws_ticket", valid_ticket)
    await passenger_router.passenger_order_stream(websocket, 987, "single-use-ticket")

    assert websocket.close_code == 4403
    assert websocket.accepted is False
    assert connection.fetchval_calls[0][1] == (987, 1001)


@pytest.mark.asyncio
async def test_order_stream_rejects_driver_ticket_before_database_access(monkeypatch):
    websocket = FakeWebSocket()
    connection = FakeConnection(owned_order=987)
    monkeypatch.setattr(passenger_router.db, "pool", FakePool(connection))

    async def driver_ticket(ticket, scope):
        assert scope == "passenger-order:987"
        return {"sub": "1002", "role": "driver"}

    monkeypatch.setattr(passenger_router, "consume_ws_ticket", driver_ticket)
    await passenger_router.passenger_order_stream(websocket, 987, "driver-ticket")

    assert websocket.close_code == 4401
    assert websocket.accepted is False
    assert connection.fetchval_calls == []


@pytest.mark.parametrize("subject", ["", "passenger", "0", "-1", "１２３", "9" * 20])
def test_passenger_claims_rejects_invalid_telegram_subject(jwt_secret, subject):
    token = make_token(jwt_secret, subject=subject)
    with pytest.raises(HTTPException) as error:
        passenger_router.passenger_claims("Bearer " + token)
    assert error.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(("order_id", "last_event_id"), [
    (0, 0), (-1, 0), (2**63, 0), (987, -1), (987, 2**63),
])
async def test_order_stream_rejects_invalid_order_or_event_cursor_before_ticket_or_database(
    monkeypatch, order_id, last_event_id
):
    websocket = FakeWebSocket()
    connection = FakeConnection(owned_order=987)
    monkeypatch.setattr(passenger_router.db, "pool", FakePool(connection))

    async def ticket_must_not_be_consumed(ticket, scope):
        raise AssertionError("invalid stream identifiers must be rejected before ticket use")

    monkeypatch.setattr(passenger_router, "consume_ws_ticket", ticket_must_not_be_consumed)
    await passenger_router.passenger_order_stream(
        websocket, order_id, "single-use-ticket", last_event_id=last_event_id
    )

    assert websocket.close_code == 4400
    assert websocket.accepted is False
    assert connection.fetchval_calls == []


@pytest.mark.asyncio
async def test_order_stream_rejects_malformed_passenger_ticket_subject_before_database(monkeypatch):
    websocket = FakeWebSocket()
    connection = FakeConnection(owned_order=987)
    monkeypatch.setattr(passenger_router.db, "pool", FakePool(connection))

    async def malformed_ticket(ticket, scope):
        return {"sub": "not-a-telegram-id", "role": "passenger"}

    monkeypatch.setattr(passenger_router, "consume_ws_ticket", malformed_ticket)
    await passenger_router.passenger_order_stream(websocket, 987, "malformed-ticket")

    assert websocket.close_code == 4401
    assert websocket.accepted is False
    assert connection.fetchval_calls == []
