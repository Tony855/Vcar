"""HTTP-level regression tests for the passenger booking API.

These tests exercise FastAPI routing, request validation, bearer authentication,
and the API-to-service boundary without claiming to replace the PostgreSQL
repository integration suite.
"""
import time
from types import SimpleNamespace

import jwt
import pytest
from fastapi.testclient import TestClient

from app.infrastructure.osrm.client import RoutingError
from app.modules.passengers import router as passenger_router
from app.services.pricing import PricingService
from app.db.repositories.order_repo import OrderRepository
from app.db.core import db
from app.web.server import app


class FakeConnection:
    def __init__(self, customer_id=41, order=None):
        self.customer_id = customer_id
        self.order = order
        self.fetchval_calls = []
        self.fetchrow_calls = []

    async def fetchval(self, query, *args):
        self.fetchval_calls.append((query, args))
        return self.customer_id

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query, args))
        return self.order


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
def api_client(monkeypatch):
    secret = "passenger-api-test-secret-" + "x" * 40
    monkeypatch.setattr(passenger_router, "settings", SimpleNamespace(secret_key=secret))
    connection = FakeConnection()
    monkeypatch.setattr(db, "pool", FakePool(connection))
    token = jwt.encode(
        {"sub": "7001001", "role": "passenger", "exp": int(time.time()) + 300},
        secret,
        algorithm="HS256",
    )
    with TestClient(app) as client:
        # Avoid application startup DB initialization; TestClient enters the
        # lifespan, so override it by using the client without a context below.
        pass
    client = TestClient(app)
    return client, connection, token


def test_booking_api_requires_passenger_bearer_token(api_client):
    client, connection, _ = api_client
    response = client.post(
        "/api/passenger/orders",
        json={
            "pickup_lat": 11.55, "pickup_lng": 104.92,
            "dropoff_lat": 11.57, "dropoff_lng": 104.95,
        },
    )
    assert response.status_code == 401
    assert connection.fetchval_calls == []


def test_booking_api_rejects_non_passenger_role(api_client):
    client, connection, _ = api_client
    secret = passenger_router.settings.secret_key
    admin_token = jwt.encode(
        {"sub": "7001001", "role": "admin", "exp": int(time.time()) + 300},
        secret,
        algorithm="HS256",
    )
    response = client.post(
        "/api/passenger/orders",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "pickup_lat": 11.55, "pickup_lng": 104.92,
            "dropoff_lat": 11.57, "dropoff_lng": 104.95,
        },
    )
    assert response.status_code == 403
    assert connection.fetchval_calls == []


def test_booking_api_validates_coordinates_before_database_access(api_client):
    client, connection, token = api_client
    response = client.post(
        "/api/passenger/orders",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "pickup_lat": 91, "pickup_lng": 104.92,
            "dropoff_lat": 11.57, "dropoff_lng": 104.95,
        },
    )
    assert response.status_code == 422
    assert connection.fetchval_calls == []


def test_booking_api_creates_order_with_priced_route(api_client, monkeypatch):
    client, connection, token = api_client
    pricing_calls = []
    create_calls = []

    async def estimate(*coordinates):
        pricing_calls.append(coordinates)
        return 6.25, 4.75

    async def create(*values):
        create_calls.append(values)
        return {"id": 991}

    monkeypatch.setattr(PricingService, "estimate", estimate)
    monkeypatch.setattr(OrderRepository, "create", create)

    response = client.post(
        "/api/passenger/orders",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "pickup_lat": 11.55, "pickup_lng": 104.92,
            "dropoff_lat": 11.57, "dropoff_lng": 104.95,
        },
    )

    assert response.status_code == 201
    assert response.json() == {
        "order_id": 991,
        "status": "DISPATCHING",
        "distance_km": 6.25,
        "price_estimate": 4.75,
    }
    assert pricing_calls == [(11.55, 104.92, 11.57, 104.95)]
    assert create_calls == [(41, 104.92, 11.55, 104.95, 11.57, 6.25, 4.75)]
    assert connection.fetchval_calls


def test_booking_api_returns_service_unavailable_when_route_quote_fails(api_client, monkeypatch):
    client, connection, token = api_client

    async def unavailable(*_coordinates):
        raise RoutingError("upstream OSRM unavailable")

    monkeypatch.setattr(PricingService, "estimate", unavailable)
    create_calls = []

    async def create(*values):
        create_calls.append(values)
        return {"id": 992}

    monkeypatch.setattr(OrderRepository, "create", create)

    response = client.post(
        "/api/passenger/orders",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "pickup_lat": 11.55, "pickup_lng": 104.92,
            "dropoff_lat": 11.57, "dropoff_lng": 104.95,
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Unable to calculate a safe route quote"
    assert create_calls == []
    assert connection.fetchval_calls
