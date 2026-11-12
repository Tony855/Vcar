"""Driver profile presence must follow the expiring heartbeat, not GEO membership."""
import time
from types import SimpleNamespace

import jwt
import pytest
from fastapi.testclient import TestClient

from app.db.core import db
from app.web.server import app


class ProfileConnection:
    async def fetchrow(self, query, *args):
        if "SELECT id, rating, plate_number FROM users" in query:
            return {"id": 12, "rating": 4.9, "plate_number": "A-123"}
        if "SELECT COALESCE(AVG(rating)" in query:
            return {"avg_r": 4.9, "total_r": 8}
        if "SELECT COUNT(*) as total_orders" in query:
            return {"total_orders": 25, "total_income": 125.0}
        raise AssertionError(f"Unexpected query: {query}")


class Acquire:
    async def __aenter__(self):
        return ProfileConnection()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class Pool:
    def acquire(self):
        return Acquire()


class Redis:
    def __init__(self, heartbeat):
        self.heartbeat = heartbeat
        self.calls = []

    async def zscore(self, key, member):
        self.calls.append((key, member))
        return self.heartbeat


@pytest.fixture
def profile_client(monkeypatch):
    secret = "driver-profile-presence-test-" + "x" * 40
    monkeypatch.setenv("SECRET_KEY", secret)
    monkeypatch.setattr(db, "pool", Pool())
    redis = Redis(time.time() - 10)
    monkeypatch.setattr(db, "redis", redis)
    token = jwt.encode(
        {"sub": "7002002", "role": "driver", "exp": int(time.time()) + 300},
        secret,
        algorithm="HS256",
    )
    return TestClient(app), redis, token


def test_driver_profile_reports_online_for_fresh_heartbeat(profile_client):
    client, redis, token = profile_client
    response = client.get(
        "/api/driver/7002002/profile",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["is_online"] == "在线"
    assert redis.calls == [("driver:heartbeat", "7002002")]


def test_driver_profile_reports_offline_when_heartbeat_is_expired(profile_client):
    client, redis, token = profile_client
    redis.heartbeat = time.time() - 180
    response = client.get(
        "/api/driver/7002002/profile",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["is_online"] == "离线"
    assert redis.calls == [("driver:heartbeat", "7002002")]


def test_driver_profile_fails_closed_when_heartbeat_is_missing(profile_client):
    client, redis, token = profile_client
    redis.heartbeat = None
    response = client.get(
        "/api/driver/7002002/profile",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["is_online"] == "离线"
    assert redis.calls == [("driver:heartbeat", "7002002")]


def test_driver_profile_fails_closed_for_future_heartbeat(profile_client):
    client, redis, token = profile_client
    redis.heartbeat = time.time() + 30
    response = client.get(
        "/api/driver/7002002/profile",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["is_online"] == "离线"
    assert redis.calls == [("driver:heartbeat", "7002002")]
