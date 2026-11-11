#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${CONFIRM_RESTORE:-}" != "YES" ]]; then
  echo "Refusing destructive restore. Set CONFIRM_RESTORE=YES after verifying a current backup." >&2
  exit 2
fi
if [[ $# -ne 1 || ! -f "$1" || ! -r "$1" ]]; then
  echo "Usage: CONFIRM_RESTORE=YES $0 /path/to/vcar-*.dump" >&2
  exit 2
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${VCAR_ENV_FILE:-$ROOT_DIR/.env}"
if [[ ! -r "$ENV_FILE" ]]; then
  echo "Environment file is missing or unreadable: $ENV_FILE" >&2
  exit 1
fi

# Restore requires schema-owner privileges. DATABASE_URL is intentionally the
# restricted vcar_app runtime role and must never be used for destructive DDL.
MIGRATION_DATABASE_URL="$(sed -n 's/^MIGRATION_DATABASE_URL=//p' "$ENV_FILE" | head -n1)"
if [[ -z "$MIGRATION_DATABASE_URL" ]]; then
  # Backward compatibility for pre-split installations only.
  MIGRATION_DATABASE_URL="$(sed -n 's/^DATABASE_URL=//p' "$ENV_FILE" | head -n1)"
fi
if [[ -z "$MIGRATION_DATABASE_URL" ]]; then
  echo "MIGRATION_DATABASE_URL (or legacy DATABASE_URL) missing from $ENV_FILE" >&2
  exit 1
fi

pg_restore --list "$1" >/dev/null

SERVICES_STOPPED=0
restart_services() {
  local status=$?
  if [[ "$SERVICES_STOPPED" -eq 1 ]]; then
    echo "Starting Vcar services after restore attempt..." >&2
    systemctl start vcar-web vcar-bot || status=1
  fi
  exit "$status"
}
trap restart_services EXIT

echo "Stopping Vcar services before restore..."
SERVICES_STOPPED=1
systemctl stop vcar-bot vcar-web
pg_restore --clean --if-exists --no-owner --no-acl --exit-on-error \
  --dbname="$MIGRATION_DATABASE_URL" "$1"

echo "Restore completed. Restarting services..."
systemctl start vcar-web vcar-bot
SERVICES_STOPPED=0
trap - EXIT
echo "Restore finished. Verify /health/ready and business workflows."
