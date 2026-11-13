import pytest
from fastapi import HTTPException

from app.modules.locations import router as locations_router


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint",
    [
        locations_router.driver_navigation_route,
        locations_router.driver_navigation_match,
    ],
)
async def test_driver_navigation_rejects_requests_when_osrm_is_disabled(monkeypatch, endpoint):
    monkeypatch.setattr(
        locations_router,
        "settings",
        type("Settings", (), {"osrm_enabled": False})(),
    )

    async def unexpected_osrm_call(*args, **kwargs):
        raise AssertionError("OSRM must not be called while the feature is disabled")

    monkeypatch.setattr(
        locations_router,
        "OSRMClient",
        unexpected_osrm_call,
        raising=False,
    )

    with pytest.raises(HTTPException) as exc:
        await endpoint(
            locations_router.NavigationRequest(
                coordinates=[(104.9282, 11.5564), (104.95, 11.57)]
            ),
            claims={"role": "driver"},
        )

    assert exc.value.status_code == 503
    assert exc.value.detail == "Navigation service is disabled"

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint,request",
    [
        (
            __import__("app.modules.routing.router", fromlist=["route"]).route,
            __import__("app.modules.routing.router", fromlist=["RouteRequest"]).RouteRequest(
                coordinates=[(104.9282, 11.5564), (104.95, 11.57)]
            ),
        ),
        (
            __import__("app.modules.routing.router", fromlist=["table"]).table,
            __import__("app.modules.routing.router", fromlist=["TableRequest"]).TableRequest(
                coordinates=[(104.9282, 11.5564), (104.95, 11.57)]
            ),
        ),
        (
            __import__("app.modules.routing.router", fromlist=["match"]).match,
            __import__("app.modules.routing.router", fromlist=["MatchRequest"]).MatchRequest(
                coordinates=[(104.9282, 11.5564), (104.95, 11.57)]
            ),
        ),
        (
            __import__("app.modules.routing.router", fromlist=["nearest"]).nearest,
            __import__("app.modules.routing.router", fromlist=["NearestRequest"]).NearestRequest(
                coordinate=(104.9282, 11.5564)
            ),
        ),
    ],
)
async def test_routing_api_rejects_requests_when_osrm_is_disabled(monkeypatch, endpoint, request):
    from app.modules.routing import router as routing_router

    monkeypatch.setattr(
        routing_router,
        "settings",
        type("Settings", (), {"osrm_enabled": False})(),
    )

    class UnexpectedClient:
        def __getattr__(self, name):
            raise AssertionError(f"OSRM {name} must not be called while disabled")

    monkeypatch.setattr(routing_router, "_client", UnexpectedClient())

    with pytest.raises(HTTPException) as exc:
        await endpoint(request)

    assert exc.value.status_code == 503
    assert exc.value.detail == "Routing service is disabled"
