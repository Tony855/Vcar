#!/usr/bin/env bash
set -Eeuo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${VCAR_ENV_FILE:-$ROOT_DIR/.env}"
BACKUP_DIR="${VCAR_BACKUP_DIR:-/var/backups/vcar}"
DATABASE_URL="$(sed -n 's/^DATABASE_URL=//p' "$ENV_FILE" | head -n1)"
if [[ -z "$DATABASE_URL" ]]; then echo "DATABASE_URL missing from $ENV_FILE" >&2; exit 1; fi
install -d -m 700 "$BACKUP_DIR"
umask 077
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TMP="$BACKUP_DIR/.vcar-$STAMP.dump.tmp"
DEST="$BACKUP_DIR/vcar-$STAMP.dump"
trap 'rm -f "$TMP"' EXIT
pg_dump --format=custom --no-owner --no-acl --file="$TMP" "$DATABASE_URL"
pg_restore --list "$TMP" >/dev/null
mv "$TMP" "$DEST"
chmod 600 "$DEST"
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'vcar-*.dump' -mtime +14 -delete
echo "Backup verified: $DEST"
