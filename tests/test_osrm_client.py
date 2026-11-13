import httpx
import pytest

from app.infrastructure.osrm.client import OSRMClient, RoutingError


@pytest.mark.asyncio
async def test_route_builds_osrm_request():
    seen = {}

    async def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"code": "Ok", "routes": [{"distance": 1234}]})

    client = OSRMClient(base_url="http://osrm", transport=httpx.MockTransport(handler))
    result = await client.route([(104.9, 11.5), (104.95, 11.55)])
    assert result["routes"][0]["distance"] == 1234
    assert "/route/v1/driving/104.900000,11.500000;104.950000,11.550000" in seen["url"]
    assert "geometries=geojson" in seen["url"]


@pytest.mark.asyncio
async def test_table_sends_sources_and_destinations():
    async def handler(request):
        assert request.url.path.startswith("/table/v1/driving/")
        assert request.url.params["sources"] == "0;1"
        assert request.url.params["destinations"] == "2"
        return httpx.Response(200, json={"code": "Ok", "durations": [[10], [20]]})

    client = OSRMClient(base_url="http://osrm", transport=httpx.MockTransport(handler))
    result = await client.table(
        [(104.9, 11.5), (104.91, 11.51), (104.92, 11.52)],
        sources=[0, 1], destinations=[2],
    )
    assert result["durations"] == [[10], [20]]


def test_rejects_invalid_coordinates():
    with pytest.raises(ValueError):
        OSRMClient._coordinates_path([(200, 11), (104, 12)])


def test_rejects_table_over_limit():
    client = OSRMClient(base_url="http://osrm", max_table_coordinates=2)
    with pytest.raises(ValueError):
        import asyncio
        asyncio.run(client.table([(104, 11), (105, 12), (106, 13)]))


@pytest.mark.asyncio
async def test_osrm_error_code_becomes_routing_error():
    async def handler(request):
        return httpx.Response(200, json={"code": "NoRoute", "message": "No route found"})

    client = OSRMClient(base_url="http://osrm", transport=httpx.MockTransport(handler))
    with pytest.raises(RoutingError):
        await client.route([(104.9, 11.5), (104.95, 11.55)])


@pytest.mark.parametrize(
    "coordinate",
    [
        (True, 11.5),
        (104.9, False),
        ("104.9", 11.5),
        (float("nan"), 11.5),
        (float("inf"), 11.5),
    ],
)
def test_rejects_boolean_string_and_non_finite_coordinates(coordinate):
    with pytest.raises(ValueError):
        OSRMClient._coordinates_path([coordinate, (104.95, 11.55)])


@pytest.mark.asyncio
@pytest.mark.parametrize("timestamps", [[1, True], [1, 2.5], [-1, 2]])
async def test_match_rejects_invalid_timestamps(timestamps):
    client = OSRMClient(base_url="http://osrm")
    with pytest.raises(ValueError, match="Timestamps"):
        await client.match([(104.9, 11.5), (104.95, 11.55)], timestamps=timestamps)


@pytest.mark.asyncio
@pytest.mark.parametrize("radiuses", [[1.0, True], [1.0, float("inf")], [1.0, 0]])
async def test_match_rejects_invalid_radiuses(radiuses):
    client = OSRMClient(base_url="http://osrm")
    with pytest.raises(ValueError, match="Radiuses"):
        await client.match([(104.9, 11.5), (104.95, 11.55)], radiuses=radiuses)


@pytest.mark.asyncio
@pytest.mark.parametrize("indexes", [[0, True], [0, 2], [-1, 1], []])
async def test_table_rejects_invalid_coordinate_indexes(indexes):
    client = OSRMClient(base_url="http://osrm")
    with pytest.raises(ValueError):
        await client.table([(104.9, 11.5), (104.95, 11.55)], sources=indexes)


@pytest.mark.asyncio
async def test_match_rejects_more_than_osrm_configured_coordinate_limit():
    client = OSRMClient(base_url="http://osrm")
    coordinates = [(104.9, 11.5)] * 101
    with pytest.raises(ValueError, match="Too many coordinates"):
        await client.match(coordinates)
