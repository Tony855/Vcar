from types import SimpleNamespace

import pytest

from app.services import pricing as pricing_module
from app.infrastructure.osrm.client import RoutingError


class FakeConnection:
    async def fetch(self, query):
        return [
            {"key": "base_fare", "value": "1.5"},
            {"key": "per_km_rate", "value": "0.75"},
        ]


class FakeAcquire:
    async def __aenter__(self):
        return FakeConnection()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakePool:
    def acquire(self):
        return FakeAcquire()


@pytest.mark.asyncio
async def test_price_estimate_uses_osrm_road_distance(monkeypatch):
    class FakeOSRM:
        async def route(self, coordinates, **kwargs):
            assert coordinates == [(104.9, 11.5), (104.95, 11.55)]
            return {"routes": [{"distance": 5400.0}]}

    monkeypatch.setattr(pricing_module, "db", SimpleNamespace(pool=FakePool()))
    monkeypatch.setattr(pricing_module, "settings", SimpleNamespace(osrm_enabled=True))
    monkeypatch.setattr(pricing_module, "OSRMClient", FakeOSRM)

    distance, price = await pricing_module.PricingService.estimate(11.5, 104.9, 11.55, 104.95)
    assert distance == 5.4
    assert price == 5.55


@pytest.mark.asyncio
async def test_price_estimate_does_not_hide_osrm_failure(monkeypatch):
    class BrokenOSRM:
        async def route(self, coordinates, **kwargs):
            raise RoutingError("offline")

    monkeypatch.setattr(pricing_module, "db", SimpleNamespace(pool=FakePool()))
    monkeypatch.setattr(pricing_module, "settings", SimpleNamespace(osrm_enabled=True))
    monkeypatch.setattr(pricing_module, "OSRMClient", BrokenOSRM)

    with pytest.raises(RoutingError):
        await pricing_module.PricingService.estimate(11.5, 104.9, 11.55, 104.95)


@pytest.mark.parametrize(
    "coordinates",
    [
        (float("nan"), 104.9, 11.55, 104.95),
        (11.5, float("inf"), 11.55, 104.95),
        (91.0, 104.9, 11.55, 104.95),
        (11.5, 181.0, 11.55, 104.95),
        (True, 104.9, 11.55, 104.95),
    ],
)
@pytest.mark.asyncio
async def test_price_estimate_rejects_invalid_coordinates_before_routing(monkeypatch, coordinates):
    monkeypatch.setattr(pricing_module, "db", SimpleNamespace(pool=FakePool()))
    monkeypatch.setattr(pricing_module, "settings", SimpleNamespace(osrm_enabled=False))
    with pytest.raises(pricing_module.PricingInputError):
        await pricing_module.PricingService.estimate(*coordinates)


@pytest.mark.parametrize("setting_value", ["nan", "inf", "-inf", "-0.01", "not-a-number"])
@pytest.mark.asyncio
async def test_price_estimate_rejects_invalid_fare_settings(monkeypatch, setting_value):
    class InvalidFareConnection:
        async def fetch(self, query):
            return [
                {"key": "base_fare", "value": setting_value},
                {"key": "per_km_rate", "value": "0.75"},
            ]

    class InvalidFareAcquire:
        async def __aenter__(self):
            return InvalidFareConnection()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        pricing_module, "db",
        SimpleNamespace(pool=SimpleNamespace(acquire=lambda: InvalidFareAcquire())),
    )
    monkeypatch.setattr(pricing_module, "settings", SimpleNamespace(osrm_enabled=False))
    with pytest.raises(pricing_module.PricingInputError):
        await pricing_module.PricingService.estimate(11.5, 104.9, 11.55, 104.95)


@pytest.mark.parametrize("distance", [-1, float("nan"), float("inf"), "invalid"])
@pytest.mark.asyncio
async def test_price_estimate_rejects_invalid_osrm_distance(monkeypatch, distance):
    class InvalidDistanceOSRM:
        async def route(self, coordinates, **kwargs):
            return {"routes": [{"distance": distance}]}

    monkeypatch.setattr(pricing_module, "db", SimpleNamespace(pool=FakePool()))
    monkeypatch.setattr(pricing_module, "settings", SimpleNamespace(osrm_enabled=True))
    monkeypatch.setattr(pricing_module, "OSRMClient", InvalidDistanceOSRM)
    with pytest.raises((RoutingError, pricing_module.PricingInputError)):
        await pricing_module.PricingService.estimate(11.5, 104.9, 11.55, 104.95)
