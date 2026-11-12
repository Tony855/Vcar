"""Smoke-test OSRM Route, Table, Match and Nearest endpoints.

Use staging coordinates explicitly when validating a downloaded regional map:
  python scripts/smoke_osrm.py --origin 104.9282,11.5564 --destination 104.9500,11.5700
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.request

BASE = os.getenv("OSRM_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
DEFAULT_ORIGIN = "104.9282,11.5564"
DEFAULT_DESTINATION = "104.9340,11.5620"


def parse_coordinate(value: str) -> tuple[float, float]:
    try:
        lon_text, lat_text = value.split(",", 1)
        lon, lat = float(lon_text), float(lat_text)
    except (ValueError, AttributeError) as exc:
        raise argparse.ArgumentTypeError("coordinate must be longitude,latitude") from exc
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise argparse.ArgumentTypeError("coordinate is outside longitude/latitude bounds")
    return lon, lat


def build_checks(origin: tuple[float, float], destination: tuple[float, float]) -> dict[str, str]:
    first = f"{origin[0]:.6f},{origin[1]:.6f}"
    second = f"{destination[0]:.6f},{destination[1]:.6f}"
    pair = f"{first};{second}"
    return {
        "nearest": f"/nearest/v1/driving/{first}?number=1",
        "route": f"/route/v1/driving/{pair}?overview=false",
        "table": f"/table/v1/driving/{pair}",
        "match": f"/match/v1/driving/{pair}?overview=false",
    }


def validate_payload(name: str, payload: object) -> bool:
    """Reject nominally successful OSRM envelopes with missing endpoint data."""
    if not isinstance(payload, dict) or payload.get("code") != "Ok":
        return False

    if name == "nearest":
        waypoints = payload.get("waypoints")
        return (
            isinstance(waypoints, list)
            and bool(waypoints)
            and isinstance(waypoints[0], dict)
            and isinstance(waypoints[0].get("location"), list)
            and len(waypoints[0]["location"]) == 2
            and all(
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
                for value in waypoints[0]["location"]
            )
            and -180 <= waypoints[0]["location"][0] <= 180
            and -90 <= waypoints[0]["location"][1] <= 90
        )

    if name == "route":
        routes = payload.get("routes")
        if not isinstance(routes, list) or not routes or not isinstance(routes[0], dict):
            return False
        route = routes[0]
        distance, duration = route.get("distance"), route.get("duration")
        return (
            isinstance(distance, (int, float))
            and not isinstance(distance, bool)
            and math.isfinite(distance)
            and distance > 0
            and isinstance(duration, (int, float))
            and not isinstance(duration, bool)
            and math.isfinite(duration)
            and duration >= 0
        )

    if name == "table":
        durations = payload.get("durations")
        distances = payload.get("distances")
        return (
            isinstance(durations, list)
            and len(durations) >= 2
            and all(isinstance(row, list) and len(row) >= 2 for row in durations)
            and isinstance(distances, list)
            and len(distances) >= 2
            and all(isinstance(row, list) and len(row) >= 2 for row in distances)
            and all(
                isinstance(matrix[i][j], (int, float))
                and not isinstance(matrix[i][j], bool)
                and math.isfinite(matrix[i][j])
                and matrix[i][j] >= 0
                for matrix in (durations, distances)
                for i, j in ((0, 1), (1, 0))
            )
        )

    if name == "match":
        matchings = payload.get("matchings")
        tracepoints = payload.get("tracepoints")
        return (
            isinstance(matchings, list)
            and bool(matchings)
            and isinstance(matchings[0], dict)
            and isinstance(matchings[0].get("distance"), (int, float))
            and math.isfinite(matchings[0]["distance"])
            and matchings[0]["distance"] >= 0
            and isinstance(tracepoints, list)
            and len(tracepoints) >= 2
            and all(isinstance(point, dict) for point in tracepoints[:2])
        )

    return False


def run_checks(base: str, checks: dict[str, str]) -> int:
    failed = []
    for name, path in checks.items():
        try:
            with urllib.request.urlopen(base.rstrip("/") + path, timeout=12) as response:
                payload = json.loads(response.read())
                status = response.status
            if status != 200 or not validate_payload(name, payload):
                code = payload.get("code") if isinstance(payload, dict) else "InvalidResponse"
                raise RuntimeError(f"unexpected OSRM response: HTTP {status}, code={code}, invalid {name} payload")
            print(f"PASS {name}")
        except (OSError, urllib.error.URLError, json.JSONDecodeError, RuntimeError) as exc:
            failed.append(name)
            print(f"FAIL {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=BASE)
    parser.add_argument("--origin", type=parse_coordinate, default=parse_coordinate(DEFAULT_ORIGIN))
    parser.add_argument("--destination", type=parse_coordinate, default=parse_coordinate(DEFAULT_DESTINATION))
    args = parser.parse_args(argv)
    return run_checks(args.base_url, build_checks(args.origin, args.destination))


if __name__ == "__main__":
    raise SystemExit(main())
