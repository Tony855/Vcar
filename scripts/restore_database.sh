#!/usr/bin/env bash
set -Eeuo pipefail
if [[ "${CONFIRM_RESTORE:-}" != "YES" ]]; then
  echo "Refusing destructive restore. Set CONFIRM_RESTORE=YES after verifying a current backup." >&2
  exit 2
fi
if [[ $# -ne 1 || ! -f "$1" ]]; then echo "Usage: CONFIRM_RESTORE=YES $0 /path/to/vcar-*.dump" >&2; exit 2; fi
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${VCAR_ENV_FILE:-$ROOT_DIR/.env}"
DATABASE_URL="$(sed -n 's/^DATABASE_URL=//p' "$ENV_FILE" | head -n1)"
if [[ -z "$DATABASE_URL" ]]; then echo "DATABASE_URL missing from $ENV_FILE" >&2; exit 1; fi
pg_restore --list "$1" >/dev/null
echo "Stopping Vcar services before restore..."
systemctl stop vcar-bot vcar-web
pg_restore --clean --if-exists --no-owner --no-acl --exit-on-error --dbname="$DATABASE_URL" "$1"
systemctl start vcar-web vcar-bot
echo "Restore finished. Verify /health/ready and business workflows."
