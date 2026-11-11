#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this installer as root." >&2
  exit 1
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
export DEBIAN_FRONTEND=noninteractive

if [[ ! -r /etc/os-release ]]; then
  echo "Cannot identify this Linux distribution; supported systems are Ubuntu 24.04+ and Debian 13+." >&2
  exit 1
fi
. /etc/os-release
case "${ID:-}" in
  ubuntu)
    if [[ "$(printf '%s\\n' "${VERSION_ID:-0}" "24.04" | sort -V | head -n1)" != "24.04" ]]; then
      echo "Ubuntu 24.04 or newer is required (detected ${VERSION_ID:-unknown})." >&2
      exit 1
    fi
    ;;
  debian)
    if [[ "$(printf '%s\\n' "${VERSION_ID:-0}" "13" | sort -V | head -n1)" != "13" ]]; then
      echo "Debian 13 or newer is required (detected ${VERSION_ID:-unknown})." >&2
      exit 1
    fi
    ;;
  *)
    echo "Unsupported Linux distribution: ${ID:-unknown}. Use Ubuntu 24.04+ or Debian 13+." >&2
    exit 1
    ;;
esac

echo "[1/7] Installing runtime dependencies..."
apt-get update -qq
curl -fsSL https://deb.nodesource.com/setup_26.x | bash - >/dev/null 2>&1
apt-get install -y -qq postgresql postgresql-client postgresql-contrib postgis redis-server python3 python3-venv python3-pip python3-dev build-essential libpq-dev curl nodejs acl >/dev/null
systemctl enable --now postgresql redis-server

python3 - <<'PY'
import sys
if not ((3, 12) <= sys.version_info[:2] < (3, 15)):
    raise SystemExit(
        f"Vcar requires Python 3.12–3.14; detected {sys.version.split()[0]}"
    )
PY
node_major="$(node --version | sed 's/^v//' | cut -d. -f1)"
if [[ "$node_major" != "26" ]]; then
  echo "Vcar frontend requires Node.js 26 LTS; detected $(node --version)." >&2
  exit 1
fi

echo "[2/7] Preparing persistent per-install secrets..."
if [[ ! -f .env ]]; then
  install -m 600 .env.example .env
fi
DB_PASSWORD="$(python3 deploy/bootstrap_env.py --env-file .env --print-db-password --write-role-sql /tmp/vcar-role-bootstrap.sql)"
MIGRATION_DB_PASSWORD="$(python3 deploy/bootstrap_env.py --env-file .env --print-migration-db-password)"
DATABASE_URL="$(grep '^DATABASE_URL=' .env | cut -d= -f2-)"
MIGRATION_DATABASE_URL="$(grep '^MIGRATION_DATABASE_URL=' .env | cut -d= -f2-)"
if [[ -z "$DB_PASSWORD" || -z "$MIGRATION_DB_PASSWORD" || -z "$DATABASE_URL" || -z "$MIGRATION_DATABASE_URL" ]]; then
  echo "Unable to initialize DATABASE_URL safely." >&2
  exit 1
fi
chown root:root .env
chmod 600 .env

echo "[3/7] Provisioning the local database without a repository default password..."
chown postgres:postgres /tmp/vcar-role-bootstrap.sql
chmod 600 /tmp/vcar-role-bootstrap.sql
if ! su - postgres -c "psql -tAc \"SELECT 1 FROM pg_roles WHERE rolname='vcar'\"" | grep -q 1; then
  su - postgres -c "createuser vcar"
fi
su - postgres -c "psql -d vcar -v ON_ERROR_STOP=1 -f /tmp/vcar-role-bootstrap.sql"
rm -f /tmp/vcar-role-bootstrap.sql
su - postgres -c "psql -tAc \"SELECT 1 FROM pg_database WHERE datname='vcar'\"" | grep -q 1 || su - postgres -c "createdb -O vcar vcar"
su - postgres -c "psql -d vcar -v ON_ERROR_STOP=1 -c \"CREATE EXTENSION IF NOT EXISTS postgis\""

echo "[4/7] Installing Python dependencies and applying migrations..."
python3 -m venv .venv
.venv/bin/pip install --disable-pip-version-check -q --upgrade pip
.venv/bin/pip install --disable-pip-version-check -q -r requirements.txt
PGPASSWORD="$MIGRATION_DB_PASSWORD" psql -h 127.0.0.1 -U vcar -d vcar -v ON_ERROR_STOP=1 -f app/db/schema.sql >/dev/null
DATABASE_URL="$DATABASE_URL" MIGRATION_DATABASE_URL="$MIGRATION_DATABASE_URL" .venv/bin/alembic upgrade head

echo "[5/7] Building the admin frontend..."
if [[ -d web_frontend ]]; then
  cd web_frontend
  npm ci --silent --registry=https://registry.npmjs.org
  NODE_OPTIONS="--max-old-space-size=1024" npm run build
  cd "$PROJECT_DIR"
  install -d app/web/static
  find app/web/static -mindepth 1 -maxdepth 1 -exec rm -rf {} +
  cp -a web_frontend/dist/. app/web/static/
fi

echo "[6/7] Creating a restricted service identity..."
if ! getent group vcar >/dev/null 2>&1; then
  groupadd --system vcar
fi
if ! id vcar >/dev/null 2>&1; then
  useradd --system --gid vcar --home-dir /nonexistent --shell /usr/sbin/nologin vcar
else
  usermod -a -G vcar vcar
fi
# Permit traversal through the checkout's parent directories without granting
# the service user interactive access to root's home.
parent="$PROJECT_DIR"
while [[ "$parent" != "/" ]]; do
  setfacl -m u:vcar:--x "$parent"
  parent="$(dirname "$parent")"
done
setfacl -R -m u:vcar:rX "$PROJECT_DIR"
# Keep migration-owner credentials out of the service account and its environment.
chown root:root .env
chmod 600 .env
setfacl -m u:vcar:--- "$PROJECT_DIR/.env"
grep -v '^MIGRATION_DATABASE_URL=' .env > .env.runtime
chown root:vcar .env.runtime
chmod 640 .env.runtime
setfacl -m u:vcar:r-- "$PROJECT_DIR/.env.runtime"
install -d -o vcar -g vcar -m 750 /var/lib/vcar
install -d -o vcar -g vcar -m 750 /var/log/vcar

echo "[7/7] Installing hardened systemd units..."
for unit in vcar-bot vcar-web; do
  cat > "/etc/systemd/system/$unit.service" <<UNIT
[Unit]
Description=Vcar ${unit#vcar-} service
After=network-online.target postgresql.service redis-server.service
Wants=network-online.target

[Service]
Type=simple
User=vcar
Group=vcar
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$PROJECT_DIR/.env.runtime
Environment=PYTHONUNBUFFERED=1
ExecStart=$PROJECT_DIR/.venv/bin/python -m app.bot_main
Restart=on-failure
RestartSec=5
TimeoutStopSec=20
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
UMask=0027
ReadWritePaths=/var/lib/vcar /var/log/vcar
UNIT
  if [[ "$unit" == "vcar-web" ]]; then
    sed -i "s#ExecStart=.*#ExecStart=$PROJECT_DIR/.venv/bin/uvicorn app.web.server:app --host 0.0.0.0 --port 8080#" "/etc/systemd/system/$unit.service"
  fi
  cat >> "/etc/systemd/system/$unit.service" <<'UNIT'
[Install]
WantedBy=multi-user.target
UNIT
done

systemctl daemon-reload
systemctl enable vcar-bot vcar-web

echo "Installation complete. Configure Bot tokens and ADMIN_PASSWORD_HASH in $PROJECT_DIR/.env, then run:"
echo "  systemctl restart vcar-bot vcar-web"
echo "No production services are restarted automatically by this script."
