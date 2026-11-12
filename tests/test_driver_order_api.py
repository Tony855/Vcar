"""HTTP contract tests for the authenticated driver order lifecycle."""
import os
import time
from types import SimpleNamespace

import jwt
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.core import db
from app.db.repositories.order_repo import OrderRepository
from app.web.server import app


class FakeConnection:
    def __init__(self, driver=None):
        self.driver = driver
        self.fetchrow_calls = []

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query, args))
        return self.driver


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
def driver_order_client(monkeypatch):
    secret = "driver-order-api-test-secret-" + "x" * 40
    monkeypatch.setenv("SECRET_KEY", secret)
    connection = FakeConnection(driver={"id": 11, "driver_status": "APPROVED"})
    monkeypatch.setattr(db, "pool", FakePool(connection))
    token = jwt.encode(
        {"sub": "7002002", "role": "driver", "exp": int(time.time()) + 300},
        secret,
        algorithm="HS256",
    )
    passenger_token = jwt.encode(
        {"sub": "7002002", "role": "passenger", "exp": int(time.time()) + 300},
        secret,
        algorithm="HS256",
    )
    return TestClient(app), connection, token, passenger_token


def test_accept_requires_driver_bearer_token(driver_order_client):
    client, connection, _, _ = driver_order_client
    response = client.post("/api/v1/driver/orders/81/accept")
    assert response.status_code == 401
    assert connection.fetchrow_calls == []


def test_accept_rejects_non_driver_role(driver_order_client):
    client, connection, _, passenger_token = driver_order_client
    response = client.post(
        "/api/v1/driver/orders/81/accept",
        headers={"Authorization": f"Bearer {passenger_token}"},
    )
    assert response.status_code == 403
    assert connection.fetchrow_calls == []


def test_accept_requires_approved_driver(driver_order_client):
    client, connection, token, _ = driver_order_client
    connection.driver = {"id": 11, "driver_status": "PENDING"}
    response = client.post(
        "/api/v1/driver/orders/81/accept",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


def test_accept_returns_accepted_order(driver_order_client, monkeypatch):
    client, connection, token, _ = driver_order_client
    calls = []

    async def accept(order_id, driver_id):
        calls.append((order_id, driver_id))
        return True

    monkeypatch.setattr(OrderRepository, "accept", accept)
    response = client.post(
        "/api/v1/driver/orders/81/accept",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json() == {"order_id": 81, "status": "ACCEPTED"}
    assert calls == [(81, 11)]
    assert connection.fetchrow_calls[0][1] == (7002002,)


def test_accept_reports_conflict_when_order_is_taken_or_driver_is_busy(driver_order_client, monkeypatch):
    client, _, token, _ = driver_order_client

    async def accept(*_args):
        return False

    monkeypatch.setattr(OrderRepository, "accept", accept)
    response = client.post(
        "/api/v1/driver/orders/81/accept",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 409


def test_driver_status_progression_is_bound_to_assigned_driver(driver_order_client, monkeypatch):
    client, _, token, _ = driver_order_client
    order = {"id": 81, "driver_id": 11, "status": "ACCEPTED"}
    calls = []

    async def get(order_id):
        assert order_id == 81
        return order.copy()

    async def update(order_id, new_status, expected_status, **kwargs):
        calls.append((order_id, new_status, expected_status, kwargs))
        return True

    monkeypatch.setattr(OrderRepository, "get", get)
    monkeypatch.setattr(OrderRepository, "update_status", update)

    for current, target in (
        ("ACCEPTED", "ARRIVED"),
        ("ARRIVED", "IN_PROGRESS"),
        ("IN_PROGRESS", "COMPLETED"),
    ):
        order["status"] = current
        response = client.patch(
            "/api/v1/driver/orders/81/status",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": target},
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"order_id": 81, "status": target}

    assert [(call[1], call[2]) for call in calls] == [
        ("ARRIVED", "ACCEPTED"),
        ("IN_PROGRESS", "ARRIVED"),
        ("COMPLETED", "IN_PROGRESS"),
    ]
    assert all(call[3] == {"actor_user_id": 11, "actor_role": "DRIVER"} for call in calls)


def test_driver_cannot_advance_another_drivers_order(driver_order_client, monkeypatch):
    client, _, token, _ = driver_order_client

    async def get(_order_id):
        return {"id": 81, "driver_id": 99, "status": "ACCEPTED"}

    async def update(*_args, **_kwargs):
        raise AssertionError("Repository transition must not run for another driver's order")

    monkeypatch.setattr(OrderRepository, "get", get)
    monkeypatch.setattr(OrderRepository, "update_status", update)
    response = client.patch(
        "/api/v1/driver/orders/81/status",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "ARRIVED"},
    )
    assert response.status_code == 404


def test_driver_status_rejects_invalid_and_stale_transitions(driver_order_client, monkeypatch):
    client, _, token, _ = driver_order_client
    order = {"id": 81, "driver_id": 11, "status": "ACCEPTED"}

    async def get(_order_id):
        return order.copy()

    async def update(*_args, **_kwargs):
        return False

    monkeypatch.setattr(OrderRepository, "get", get)
    monkeypatch.setattr(OrderRepository, "update_status", update)

    invalid = client.patch(
        "/api/v1/driver/orders/81/status",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "COMPLETED"},
    )
    assert invalid.status_code == 409

    malformed = client.patch(
        "/api/v1/driver/orders/81/status",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "CANCELLED"},
    )
    assert malformed.status_code == 422

    stale = client.patch(
        "/api/v1/driver/orders/81/status",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "ARRIVED"},
    )
    assert stale.status_code == 409
