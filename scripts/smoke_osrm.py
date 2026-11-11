"""Smoke-test the OSRM Route, Table, Match and Nearest HTTP endpoints."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.getenv("OSRM_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
CHECKS = {
    "nearest": "/nearest/v1/driving/104.9282,11.5564?number=1",
    "route": "/route/v1/driving/104.9282,11.5564;104.9340,11.5620?overview=false",
    "table": "/table/v1/driving/104.9282,11.5564;104.9340,11.5620",
    "match": "/match/v1/driving/104.9282,11.5564;104.9340,11.5620?overview=false",
}


def main() -> int:
    failed = []
    for name, path in CHECKS.items():
        try:
            with urllib.request.urlopen(BASE + path, timeout=12) as response:
                payload = json.loads(response.read())
            if response.status != 200 or payload.get("code") != "Ok":
                raise RuntimeError(f"unexpected OSRM code: {payload.get('code')}")
            print(f"PASS {name}")
        except (OSError, urllib.error.URLError, json.JSONDecodeError, RuntimeError) as exc:
            failed.append(name)
            print(f"FAIL {name}: {exc}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
