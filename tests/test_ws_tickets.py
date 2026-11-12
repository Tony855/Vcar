import json
import time
from types import SimpleNamespace

import jwt
import pytest
from fastapi import HTTPException

from app.modules.auth import ws_tickets


class FakeRedis:
    def __init__(self):
        self.values = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True

    async def getdel(self, key):
        return self.values.pop(key, None)


def token(secret, role="admin", subject="12345"):
    return jwt.encode(
        {"sub": subject, "role": role, "exp": int(time.time()) + 300},
        secret,
        algorithm="HS256",
    )


@pytest.fixture
def fake_redis(monkeypatch):
    redis = FakeRedis()
    secret = "ws-ticket-test-secret-" + "x" * 40
    monkeypatch.setattr(ws_tickets, "settings", SimpleNamespace(secret_key=secret))
    monkeypatch.setattr(ws_tickets.db, "redis", redis)
    return redis, secret


@pytest.mark.asyncio
async def test_ticket_is_short_lived_single_use_and_scope_bound(fake_redis):
    redis, secret = fake_redis
    access_token = token(secret)
    ticket = await ws_tickets.issue_ws_ticket(access_token, "admin-fleet")
    claims = await ws_tickets.consume_ws_ticket(ticket, "admin-fleet")
    assert claims == {"sub": "12345", "role": "admin", "scope": "admin-fleet"}
    assert await ws_tickets.consume_ws_ticket(ticket, "admin-fleet") is None
    assert len(redis.values) == 0


@pytest.mark.asyncio
async def test_ticket_cannot_be_used_for_another_scope(fake_redis):
    _, secret = fake_redis
    ticket = await ws_tickets.issue_ws_ticket(token(secret), "admin-fleet")
    assert await ws_tickets.consume_ws_ticket(ticket, "driver-location") is None
    assert await ws_tickets.consume_ws_ticket(ticket, "admin-fleet") is None


@pytest.mark.asyncio
async def test_ticket_issuer_rejects_wrong_role_and_invalid_scope(fake_redis):
    _, secret = fake_redis
    with pytest.raises(PermissionError):
        await ws_tickets.issue_ws_ticket(token(secret, role="driver"), "admin-fleet")
    with pytest.raises(ValueError):
        await ws_tickets.issue_ws_ticket(token(secret), "unrestricted")


@pytest.mark.asyncio
async def test_ticket_issuer_rejects_malformed_driver_and_passenger_subjects(fake_redis):
    _, secret = fake_redis
    for role, scope in (
        ("driver", "driver-location"),
        ("passenger", "passenger-order:42"),
    ):
        for subject in ("not-a-number", "", "-12", "0", "9" * 20):
            with pytest.raises(PermissionError):
                await ws_tickets.issue_ws_ticket(
                    token(secret, role=role, subject=subject), scope
                )


@pytest.mark.asyncio
async def test_ticket_issuer_accepts_valid_driver_and_passenger_subjects(fake_redis):
    _, secret = fake_redis
    driver_ticket = await ws_tickets.issue_ws_ticket(
        token(secret, role="driver", subject="12345"), "driver-location"
    )
    passenger_ticket = await ws_tickets.issue_ws_ticket(
        token(secret, role="passenger", subject="67890"), "passenger-order:42"
    )
    assert driver_ticket and passenger_ticket


@pytest.mark.asyncio
async def test_ticket_consumer_rejects_non_object_json(fake_redis):
    redis, _ = fake_redis
    ticket = "malformed-json-object"
    redis.values[ws_tickets._ticket_key(ticket)] = json.dumps(["not", "an", "object"])
    assert await ws_tickets.consume_ws_ticket(ticket, "admin-fleet") is None


@pytest.mark.asyncio
async def test_ticket_consumer_rejects_malformed_telegram_subject(fake_redis):
    redis, _ = fake_redis
    ticket = "malformed-driver-subject"
    redis.values[ws_tickets._ticket_key(ticket)] = json.dumps({
        "sub": "not-a-number", "role": "driver", "scope": "driver-location"
    })
    assert await ws_tickets.consume_ws_ticket(ticket, "driver-location") is None


@pytest.mark.asyncio
async def test_ticket_issuer_rejects_access_token_without_exp(fake_redis):
    _, secret = fake_redis
    token_without_exp = jwt.encode(
        {"sub": "12345", "role": "admin"},
        secret,
        algorithm="HS256",
    )
    with pytest.raises(ValueError, match="Invalid or expired"):
        await ws_tickets.issue_ws_ticket(token_without_exp, "admin-fleet")


@pytest.mark.asyncio
async def test_ticket_issuer_rejects_weak_signing_secret(fake_redis, monkeypatch):
    _, _ = fake_redis
    weak_secret = "weak"
    monkeypatch.setattr(ws_tickets, "settings", SimpleNamespace(secret_key=weak_secret))
    token_with_weak_key = jwt.encode(
        {"sub": "12345", "role": "admin", "exp": int(time.time()) + 300},
        weak_secret,
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as error:
        await ws_tickets.issue_ws_ticket(token_with_weak_key, "admin-fleet")
    assert error.value.status_code == 503
