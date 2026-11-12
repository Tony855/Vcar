import pytest
from pydantic import ValidationError

from app.modules.routing.router import (
    MatchRequest,
    NearestRequest,
    RouteRequest,
    TableRequest,
)


PAIR = [[104.9, 11.5], [104.95, 11.55]]


@pytest.mark.parametrize("bad", [
    [[True, 11.5], [104.95, 11.55]],
    [["104.9", 11.5], [104.95, 11.55]],
    [[float("nan"), 11.5], [104.95, 11.55]],
    [[float("inf"), 11.5], [104.95, 11.55]],
    [[181, 11.5], [104.95, 11.55]],
    [[104.9, -91], [104.95, 11.55]],
])
def test_route_request_rejects_coerced_or_invalid_coordinates(bad):
    with pytest.raises(ValidationError):
        RouteRequest.model_validate({"coordinates": bad})


def test_route_request_accepts_integer_coordinates_without_coercion():
    request = RouteRequest.model_validate({"coordinates": [[104, 11], [105, 12]]})
    assert request.coordinates == [(104.0, 11.0), (105.0, 12.0)]


@pytest.mark.parametrize("bad", [[0, True], [0, 1.5], [0, -1], [0, 2], []])
def test_table_request_rejects_invalid_source_indexes(bad):
    with pytest.raises(ValidationError):
        TableRequest.model_validate({"coordinates": PAIR, "sources": bad})


@pytest.mark.parametrize("bad", [[1, True], [1, 2.5], [-1, 2]])
def test_match_request_rejects_invalid_timestamps(bad):
    with pytest.raises(ValidationError):
        MatchRequest.model_validate({"coordinates": PAIR, "timestamps": bad})


@pytest.mark.parametrize("bad", [[1, True], [1, float("inf")], [1, float("nan")], [1, 0]])
def test_match_request_rejects_invalid_radiuses(bad):
    with pytest.raises(ValidationError):
        MatchRequest.model_validate({"coordinates": PAIR, "radiuses": bad})


@pytest.mark.parametrize("bad", [[True, 11.5], ["104.9", 11.5], [float("nan"), 11.5], [181, 11.5]])
def test_nearest_request_rejects_invalid_coordinate(bad):
    with pytest.raises(ValidationError):
        NearestRequest.model_validate({"coordinate": bad})


@pytest.mark.parametrize("bad", [True, 1.5, "2"])
def test_nearest_request_rejects_non_integer_number(bad):
    with pytest.raises(ValidationError):
        NearestRequest.model_validate({"coordinate": [104.9, 11.5], "number": bad})
