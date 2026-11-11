#!/usr/bin/env bash
set -Eeuo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="$ROOT_DIR/data/osrm"
PBF_URL="https://download.geofabrik.de/asia/cambodia-latest.osm.pbf"
PBF_FILE="$DATA_DIR/cambodia-latest.osm.pbf"
mkdir -p "$DATA_DIR"
command -v curl >/dev/null 2>&1 || { echo "curl is required" >&2; exit 1; }
tmp_file="$PBF_FILE.tmp"
trap 'rm -f "$tmp_file"' EXIT
curl --fail --location --retry 3 --output "$tmp_file" "$PBF_URL"
test -s "$tmp_file"
mv "$tmp_file" "$PBF_FILE"
echo "Downloaded Cambodia OSM extract to $PBF_FILE"
echo "Next: docker compose -f docker-compose.osrm.yml --profile prepare run --rm osrm-prepare"
