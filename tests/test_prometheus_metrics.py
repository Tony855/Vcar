from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.web import server


class MetricsConnection:
    async def fetchval(self, query):
        if "COUNT(*) FROM order_event_outbox" in query:
            return 3
        if "MIN(created_at)" in query:
            return 42.5
        if "COUNT(*) FROM driver_locations" in query:
            return 8
        if "LEFT JOIN driver_locations" in query:
            return 2
        raise AssertionError(query)


class Acquire:
    async def __aenter__(self):
        return MetricsConnection()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class Pool:
    def acquire(self):
        return Acquire()


@pytest.mark.asyncio
async def test_metrics_requires_dedicated_bearer_token(monkeypatch):
    monkeypatch.setattr(server, "settings", SimpleNamespace(metrics_bearer_token="private-token"))
    with pytest.raises(HTTPException) as error:
        await server.prometheus_metrics("Bearer wrong-token")
    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_metrics_endpoint_is_disabled_without_token(monkeypatch):
    monkeypatch.setattr(server, "settings", SimpleNamespace(metrics_bearer_token=""))
    with pytest.raises(HTTPException) as error:
        await server.prometheus_metrics(None)
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_metrics_returns_outbox_and_gps_gauges(monkeypatch):
    monkeypatch.setattr(server, "settings", SimpleNamespace(metrics_bearer_token="private-token"))
    monkeypatch.setattr(server.db, "pool", Pool())

    response = await server.prometheus_metrics("Bearer private-token")
    body = response.body.decode()

    assert response.status_code == 200
    assert "vcar_order_event_outbox_pending 3" in body
    assert "vcar_order_event_outbox_oldest_age_seconds 42.500" in body
    assert "vcar_driver_locations_fresh 8" in body
    assert "vcar_driver_locations_stale 2" in body
    assert body.endswith("\n")
