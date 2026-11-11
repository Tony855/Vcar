#!/usr/bin/env bash
set -Eeuo pipefail

OSRM_BASE_URL="${OSRM_BASE_URL:-http://127.0.0.1:5000}"
OSRM_BASE_URL="${OSRM_BASE_URL%/}"
python3 - "$OSRM_BASE_URL" <<'PY'
import json
import sys
import urllib.error
import urllib.request

base = sys.argv[1]
checks = {
    "nearest": "/nearest/v1/driving/104.9282,11.5564?number=1",
    "route": "/route/v1/driving/104.9282,11.5564;104.9340,11.5620?overview=false",
    "table": "/table/v1/driving/104.9282,11.5564;104.9340,11.5620",
    "match": "/match/v1/driving/104.9282,11.5564;104.9340,11.5620?overview=false",
}
failed = []
for name, path in checks.items():
    try:
        with urllib.request.urlopen(base + path, timeout=12) as response:
            payload = json.loads(response.read())
        if response.status != 200 or payload.get("code") != "Ok":
            raise RuntimeError(f"unexpected response code: {payload.get('code')}")
        print(f"PASS {name}")
    except (OSError, urllib.error.URLError, json.JSONDecodeError, RuntimeError) as exc:
        failed.append((name, str(exc)))
        print(f"FAIL {name}: {exc}", file=sys.stderr)
if failed:
    raise SystemExit(1)
PY
