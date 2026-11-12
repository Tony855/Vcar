#!/usr/bin/env bash
set -Eeuo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOAD_SCRIPT="$ROOT_DIR/scripts/load_test_locations.py"
BASE_URL="${VCAR_BASE_URL:-}"
OUTPUT_DIR="${VCAR_LOAD_OUTPUT_DIR:-$ROOT_DIR/artifacts/gps-load}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
MESSAGES="${VCAR_LOAD_MESSAGES:-3}"
RECONNECT_PROBES="${VCAR_RECONNECT_PROBES:-5}"
MIN_RATE="${VCAR_MIN_DELIVERY_RATE:-0.99}"
MAX_CONNECT_P95="${VCAR_MAX_CONNECT_P95_MS:-}"
MAX_FANOUT_P95="${VCAR_MAX_FANOUT_P95_MS:-}"
COOLDOWN="${VCAR_LOAD_COOLDOWN_SECONDS:-30}"

if [[ "${VCAR_STAGING_ACK:-}" != "I_CONFIRM_ISOLATED_STAGING" ]]; then
  echo "Refusing load test: set VCAR_STAGING_ACK=I_CONFIRM_ISOLATED_STAGING only for isolated staging." >&2
  exit 2
fi
if [[ -z "$BASE_URL" || ! "$BASE_URL" =~ ^https?:// ]]; then
  echo "Set VCAR_BASE_URL to the isolated staging HTTP(S) base URL." >&2
  exit 2
fi
if [[ -z "${DRIVER_TOKEN:-}" || -z "${ADMIN_TOKEN:-}" ]]; then
  echo "DRIVER_TOKEN and ADMIN_TOKEN must be short-lived staging JWTs." >&2
  exit 2
fi
if [[ ! "$MESSAGES" =~ ^[1-9][0-9]*$ || ! "$RECONNECT_PROBES" =~ ^[0-9]+$ || ! "$COOLDOWN" =~ ^[0-9]+$ ]]; then
  echo "Messages must be positive; reconnect probes and cooldown must be non-negative integers." >&2
  exit 2
fi
LATENCY_ARGS=()
for setting in "$MAX_CONNECT_P95" "$MAX_FANOUT_P95"; do
  if [[ -n "$setting" ]] && { [[ ! "$setting" =~ ^[0-9]+([.][0-9]+)?$ ]] || ! awk -v value="$setting" 'BEGIN { exit !(value > 0) }'; }; then
    echo "Configured p95 latency ceilings must be positive numbers." >&2
    exit 2
  fi
done
[[ -z "$MAX_CONNECT_P95" ]] || LATENCY_ARGS+=(--max-connect-p95-ms "$MAX_CONNECT_P95")
[[ -z "$MAX_FANOUT_P95" ]] || LATENCY_ARGS+=(--max-fanout-p95-ms "$MAX_FANOUT_P95")

mkdir -p "$OUTPUT_DIR"
chmod 700 "$OUTPUT_DIR"
printf 'Starting staged GPS/WebSocket load profiles against %s\n' "$BASE_URL"
printf 'Reports: %s\n' "$OUTPUT_DIR"

for connections in 50 100 250 500; do
  report="$OUTPUT_DIR/gps-${connections}.json"
  printf '\n=== %s concurrent admin fleet subscribers ===\n' "$connections"
  "$PYTHON_BIN" "$LOAD_SCRIPT" \
    --base-url "$BASE_URL" \
    --connections "$connections" \
    --messages "$MESSAGES" \
    --reconnect-probes "$RECONNECT_PROBES" \
    --min-delivery-rate "$MIN_RATE" \
    --json-output "$report"
  if [[ ! -s "$report" ]]; then
    echo "Load profile $connections did not produce a report; stopping." >&2
    exit 1
  fi
  printf 'Profile %s passed; report saved.\n' "$connections"
  if [[ "$connections" != 500 && "$COOLDOWN" -gt 0 ]]; then
    sleep "$COOLDOWN"
  fi
done
printf '\nAll four staging profiles passed. Review host telemetry and cross-account isolation before sign-off.\n'
