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
        def read(self): return json.dumps({"code": "Ok"}).encode()

    monkeypatch.setattr(smoke_osrm.urllib.request, "urlopen", lambda *a, **k: Response())
    assert smoke_osrm.run_checks("http://osrm", {"route": "/route"}) == 0
    assert "PASS route" in capsys.readouterr().out

    class BadResponse(Response):
        def read(self): return json.dumps({"code": "NoRoute"}).encode()
    monkeypatch.setattr(smoke_osrm.urllib.request, "urlopen", lambda *a, **k: BadResponse())
    assert smoke_osrm.run_checks("http://osrm", {"route": "/route"}) == 1
