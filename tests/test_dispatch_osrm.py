from types import SimpleNamespace

import pytest

from app.services import dispatch as dispatch_module


class FakeConnection:
    async def fetch(self, query, *args):
        if "SELECT DISTINCT driver_id" in query:
            return []
        return [
            {"id": 1, "telegram_id": 1001, "rating": 5.0, "locale": "zh"},
            {"id": 2, "telegram_id": 1002, "rating": 5.0, "locale": "en"},
        ]


class FakeAcquire:
    async def __aenter__(self):
        return FakeConnection()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakePool:
    def acquire(self):
        return FakeAcquire()


class FakeRedis:
    async def geosearch(self, *args, **kwargs):
        return [
            ("1001", "0.5", ("104.9000", "11.5000")),
            ("1002", "2.0", ("104.9100", "11.5100")),
        ]


@pytest.mark.asyncio
async def test_dispatch_prefers_best_osrm_eta(monkeypatch):
    class FakeOSRM:
        async def table(self, coordinates, **kwargs):
            assert kwargs["sources"] == [0, 1]
            assert kwargs["destinations"] == [2]
            return {"durations": [[900.0], [240.0]]}

    monkeypatch.setattr(dispatch_module, "db", SimpleNamespace(pool=FakePool(), redis=FakeRedis()))
    monkeypatch.setattr(dispatch_module, "settings", SimpleNamespace(osrm_enabled=True))
    monkeypatch.setattr(dispatch_module, "OSRMClient", FakeOSRM)

    result = await dispatch_module.DispatchService.smart_dispatch(104.95, 11.55)
    assert [item["tg_id"] for item in result] == [1002, 1001]
    assert result[0]["eta_seconds"] == 240.0


@pytest.mark.asyncio
async def test_dispatch_falls_back_to_geo_distance_if_osrm_is_down(monkeypatch):
    class BrokenOSRM:
        async def table(self, coordinates, **kwargs):
            from app.infrastructure.osrm.client import RoutingError
            raise RoutingError("offline")

    monkeypatch.setattr(dispatch_module, "db", SimpleNamespace(pool=FakePool(), redis=FakeRedis()))
    monkeypatch.setattr(dispatch_module, "settings", SimpleNamespace(osrm_enabled=True))
    monkeypatch.setattr(dispatch_module, "OSRMClient", BrokenOSRM)

    result = await dispatch_module.DispatchService.smart_dispatch(104.95, 11.55)
    assert [item["tg_id"] for item in result] == [1001, 1002]
    assert all(item["eta_seconds"] is None for item in result)
