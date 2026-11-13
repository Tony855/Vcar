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
