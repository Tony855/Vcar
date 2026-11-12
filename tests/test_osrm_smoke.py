import json
from types import SimpleNamespace

import pytest

from scripts import smoke_osrm


def test_parse_coordinate_accepts_longitude_latitude():
    assert smoke_osrm.parse_coordinate("104.9282,11.5564") == (104.9282, 11.5564)


@pytest.mark.parametrize("value", ["11.5", "181,0", "0,-91", "x,y"])
def test_parse_coordinate_rejects_invalid_values(value):
    with pytest.raises(Exception):
        smoke_osrm.parse_coordinate(value)


def test_build_checks_uses_supplied_regional_coordinates():
    checks = smoke_osrm.build_checks((103.1, 12.2), (104.2, 13.3))
    assert "103.100000,12.200000" in checks["nearest"]
    assert "103.100000,12.200000;104.200000,13.300000" in checks["route"]


def test_run_checks_requires_all_osrm_responses_to_be_ok(monkeypatch, capsys):
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return json.dumps({"code": "Ok", "routes": [{"distance": 100, "duration": 20}]}).encode()

    monkeypatch.setattr(smoke_osrm.urllib.request, "urlopen", lambda *a, **k: Response())
    assert smoke_osrm.run_checks("http://osrm", {"route": "/route"}) == 0
    assert "PASS route" in capsys.readouterr().out

    class BadResponse(Response):
        def read(self): return json.dumps({"code": "NoRoute"}).encode()
    monkeypatch.setattr(smoke_osrm.urllib.request, "urlopen", lambda *a, **k: BadResponse())
    assert smoke_osrm.run_checks("http://osrm", {"route": "/route"}) == 1


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("nearest", {"code": "Ok", "waypoints": [{"location": [104.9, 11.5]}]}),
        ("route", {"code": "Ok", "routes": [{"distance": 100, "duration": 20}]}),
        ("table", {"code": "Ok", "durations": [[0, 20], [20, 0]], "distances": [[0, 100], [100, 0]]}),
        ("match", {"code": "Ok", "matchings": [{"distance": 100}], "tracepoints": [{}, {}]}),
    ],
)
def test_validate_payload_accepts_complete_endpoint_results(name, payload):
    assert smoke_osrm.validate_payload(name, payload)


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("nearest", {"code": "Ok", "waypoints": []}),
        ("route", {"code": "Ok", "routes": []}),
        ("route", {"code": "Ok", "routes": [{"distance": 0, "duration": 0}]}),
        ("table", {"code": "Ok", "durations": [[0, None], [None, 0]], "distances": [[0, None], [None, 0]]}),
        ("match", {"code": "Ok", "matchings": [], "tracepoints": [None, None]}),
        ("unknown", {"code": "Ok"}),
        ("route", {"code": "NoRoute", "routes": []}),
    ],
)
def test_validate_payload_rejects_success_envelopes_without_usable_routes(name, payload):
    assert not smoke_osrm.validate_payload(name, payload)


def test_run_checks_rejects_ok_envelope_with_empty_route_payload(monkeypatch, capsys):
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return json.dumps({"code": "Ok", "routes": []}).encode()

    monkeypatch.setattr(smoke_osrm.urllib.request, "urlopen", lambda *a, **k: Response())
    assert smoke_osrm.run_checks("http://osrm", {"route": "/route"}) == 1
    assert "FAIL route" in capsys.readouterr().err


def test_validate_payload_rejects_non_finite_route_and_table_metrics():
    assert not smoke_osrm.validate_payload(
        "route", {"code": "Ok", "routes": [{"distance": float("inf"), "duration": 1}]}
    )
    assert not smoke_osrm.validate_payload(
        "table",
        {"code": "Ok", "durations": [[0, float("inf")], [1, 0]], "distances": [[0, 1], [1, 0]]},
    )


def test_validate_payload_rejects_invalid_nearest_coordinate():
    assert not smoke_osrm.validate_payload(
        "nearest", {"code": "Ok", "waypoints": [{"location": [float("nan"), 11.5]}]}
    )


def test_validate_payload_rejects_boolean_match_distance():
    assert not smoke_osrm.validate_payload(
        "match", {"code": "Ok", "matchings": [{"distance": True}], "tracepoints": [{}, {}]}
    )



@pytest.mark.parametrize(
    "value",
    ["nan,11.5", "inf,11.5", "-inf,11.5", "104.9,nan", "104.9,inf"],
)
def test_parse_coordinate_rejects_non_finite_values(value):
    with pytest.raises(Exception):
        smoke_osrm.parse_coordinate(value)
