"""HTTP-level tests for driver authentication and GPS ingestion."""
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import urlencode

import jwt
import pytest
from fastapi.testclient import TestClient

from app.db.core import db
from app.modules.auth import router as driver_auth_router
from app.modules.locations import router as location_router
from app.web.server import app


class FakeConnection:
    def __init__(self, *, driver=None, latest=None, inserted=None, active_order=None):
        self.driver = driver
        self.latest = latest
        self.inserted = inserted
        self.active_order = active_order
        self.fetchrow_calls = []
        self.fetchval_calls = []
        self.execute_calls = []

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query, args))
        if "SELECT id FROM users" in query:
            return self.driver
        if "INSERT INTO driver_locations" in query:
            return self.inserted
        return None

    async def fetchval(self, query, *args):
        self.fetchval_calls.append((query, args))
        if "SELECT recorded_at FROM driver_locations" in query:
            return self.latest
        if "SELECT id FROM orders" in query:
            return self.active_order
        return None

    async def execute(self, query, *args):
        self.execute_calls.append((query, args))
        return "INSERT 0 1"


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


@pytest.fixture
def driver_client(monkeypatch):
    secret = "driver-api-test-secret-" + "x" * 40
    monkeypatch.setenv("SECRET_KEY", secret)
    connection = FakeConnection(
        driver={"id": 11},
        inserted={"driver_id": 11},
    )
    monkeypatch.setattr(db, "pool", FakePool(connection))
    monkeypatch.setattr(db, "redis", object())
    token = jwt.encode(
        {"sub": "7002002", "role": "driver", "exp": int(time.time()) + 300},
        secret,
        algorithm="HS256",
    )
    return TestClient(app), connection, token, secret


def _signed_telegram_init_data(bot_token, user):
    values = {
        "auth_date": str(int(time.time())),
        "user": json.dumps(user, separators=(",", ":")),
    }
    check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret_key = hmac.new(bot_token.encode(), b"WebAppData", hashlib.sha256).digest()
    values["hash"] = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


def test_driver_session_requires_approved_driver_and_returns_driver_token(driver_client, monkeypatch):
    client, connection, _, secret = driver_client
    monkeypatch.setattr(
        driver_auth_router,
        "settings",
        SimpleNamespace(driver_bot_token="driver-test-bot-token"),
    )
    connection.driver = {"driver_status": "APPROVED"}
    init_data = _signed_telegram_init_data(
        "driver-test-bot-token",
        {"id": 7002002, "first_name": "Approved Driver"},
    )

    response = client.post("/api/driver/session", json={"init_data": init_data})

    assert response.status_code == 200
    payload = response.json()
    assert payload["tg_id"] == 7002002
    assert payload["expires_in"] == 1200
    claims = jwt.decode(payload["token"], secret, algorithms=["HS256"])
    assert claims["sub"] == "7002002"
    assert claims["role"] == "driver"
    assert len(connection.fetchrow_calls) == 1


def test_driver_session_rejects_unapproved_driver(driver_client, monkeypatch):
    client, connection, _, _ = driver_client
    monkeypatch.setattr(
        driver_auth_router,
        "settings",
        SimpleNamespace(driver_bot_token="driver-test-bot-token"),
    )
    connection.driver = {"driver_status": "PENDING"}
    init_data = _signed_telegram_init_data(
        "driver-test-bot-token",
        {"id": 7002002, "first_name": "Pending Driver"},
    )

    response = client.post("/api/driver/session", json={"init_data": init_data})

    assert response.status_code == 403


def test_gps_api_requires_driver_role(driver_client):
    client, connection, _, secret = driver_client
    passenger_token = jwt.encode(
        {"sub": "7002002", "role": "passenger", "exp": int(time.time()) + 300},
        secret,
        algorithm="HS256",
    )
    response = client.post(
        "/api/v1/driver/location",
        headers={"Authorization": f"Bearer {passenger_token}"},
        json={
            "latitude": 11.55, "longitude": 104.92,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert response.status_code == 403
    assert connection.fetchrow_calls == []


def test_gps_api_persists_approved_driver_sample_and_publishes_realtime(driver_client, monkeypatch):
    client, connection, token, _ = driver_client
    published = []

    async def publish(redis, **kwargs):
        published.append((redis, kwargs))
        return True

    monkeypatch.setattr(location_router, "publish_driver_location", publish)
    recorded_at = datetime.now(timezone.utc).isoformat()
    response = client.post(
        "/api/v1/driver/location",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "latitude": 11.55,
            "longitude": 104.92,
            "accuracy_m": 8,
            "heading_deg": 90,
            "speed_mps": 4,
            "recorded_at": recorded_at,
        },
    )

    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert response.json()["realtime_published"] is True
    assert len(connection.fetchrow_calls) == 2
    assert len(connection.fetchval_calls) == 2
    assert len(published) == 1
    assert published[0][1]["telegram_id"] == 7002002
    assert published[0][1]["longitude"] == 104.92
    assert published[0][1]["latitude"] == 11.55


def test_gps_api_rejects_stale_sample_without_insert_or_publish(driver_client, monkeypatch):
    client, connection, token, _ = driver_client
    connection.latest = datetime.now(timezone.utc)
    published = []

    async def publish(*args, **kwargs):
        published.append(kwargs)
        return True

    monkeypatch.setattr(location_router, "publish_driver_location", publish)
    stale_time = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    response = client.post(
        "/api/v1/driver/location",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "latitude": 11.55,
            "longitude": 104.92,
            "recorded_at": stale_time,
        },
    )

    assert response.status_code == 409
    assert len(connection.fetchrow_calls) == 1
    assert len(connection.fetchval_calls) == 1
    assert published == []
