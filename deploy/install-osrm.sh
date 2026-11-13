#!/usr/bin/env bash
set -Eeuo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
if [[ "$EUID" -ne 0 ]]; then echo "Run this installer as root." >&2; exit 1; fi
cd "$ROOT_DIR"
command -v apt-get >/dev/null || { echo "OSRM installer supports Ubuntu/Debian only." >&2; exit 1; }
apt-get update -qq
apt-get install -y -qq curl
if ! command -v docker >/dev/null 2>&1; then
  echo "Installing Docker Engine and Compose plugin..."
  apt-get update -qq
  apt-get install -y docker.io docker-compose-v2 curl
  systemctl enable --now docker
fi
docker compose version >/dev/null
if [[ -s data/osrm/cambodia-latest.osrm.mldgr ]]; then
  read -r -p "Cambodia routing data already exists. Reuse it? [Y/n] " reuse
  if [[ "$reuse" =~ ^[Nn]$ ]]; then
    bash scripts/download_map_data.sh
    docker compose -f docker-compose.osrm.yml --profile prepare run --rm osrm-prepare
  fi
else
  echo "This optional step downloads the Cambodia OSM extract and preprocesses it."
  bash scripts/download_map_data.sh
  docker compose -f docker-compose.osrm.yml --profile prepare run --rm osrm-prepare
fi
docker compose -f docker-compose.osrm.yml up -d osrm
ready=0
for attempt in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:5000/nearest/v1/driving/104.9282,11.5564?number=1" >/dev/null; then ready=1; break; fi
  sleep 2
done
if [[ "$ready" -ne 1 ]]; then
  docker compose -f docker-compose.osrm.yml logs --tail=80 osrm >&2
  echo "OSRM did not become ready." >&2
  exit 1
fi
python3 scripts/smoke_osrm.py --base-url http://127.0.0.1:5000
echo "OSRM is ready on 127.0.0.1:5000. Main Vcar installation does not require OSRM."
