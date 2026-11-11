#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="${VCAR_HEALTH_BASE_URL:-http://127.0.0.1:8080}"
TIMEOUT="${VCAR_HEALTH_TIMEOUT_SECONDS:-5}"

for endpoint in /health/live /health/ready; do
  if ! curl --fail --silent --show-error --max-time "$TIMEOUT" \
      --output /dev/null "$BASE_URL$endpoint"; then
    echo "Vcar health check failed: $endpoint" >&2
    exit 1
  fi
done

echo "Vcar liveness and readiness checks passed."
