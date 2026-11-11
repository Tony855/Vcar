#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${VCAR_ENV_FILE:-$ROOT_DIR/.env}"
BACKUP_DIR="${VCAR_BACKUP_DIR:-/var/backups/vcar}"

if [[ ! -r "$ENV_FILE" ]]; then
  echo "Environment file is missing or unreadable: $ENV_FILE" >&2
  exit 1
fi

# A complete schema backup must use the schema owner, not the restricted
# vcar_app runtime account. Keep legacy fallback for pre-split installations.
BACKUP_DATABASE_URL="$(sed -n 's/^MIGRATION_DATABASE_URL=//p' "$ENV_FILE" | head -n1)"
if [[ -z "$BACKUP_DATABASE_URL" ]]; then
  BACKUP_DATABASE_URL="$(sed -n 's/^DATABASE_URL=//p' "$ENV_FILE" | head -n1)"
fi
if [[ -z "$BACKUP_DATABASE_URL" ]]; then
  echo "MIGRATION_DATABASE_URL (or legacy DATABASE_URL) missing from $ENV_FILE" >&2
  exit 1
fi

install -d -m 700 "$BACKUP_DIR"
umask 077
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
# mktemp makes concurrent backups unique even when both start in the same second.
TMP="$(mktemp "$BACKUP_DIR/.vcar-$STAMP.XXXXXX.dump.tmp")"
UNIQUE="$(basename "$TMP")"
UNIQUE="${UNIQUE#.vcar-$STAMP.}"
UNIQUE="${UNIQUE%.dump.tmp}"
DEST="$BACKUP_DIR/vcar-$STAMP-$UNIQUE.dump"
trap 'rm -f "$TMP"' EXIT

pg_dump --format=custom --no-owner --no-acl --file="$TMP" "$BACKUP_DATABASE_URL"
pg_restore --list "$TMP" >/dev/null
mv "$TMP" "$DEST"
chmod 600 "$DEST"
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'vcar-*.dump' -mtime +14 -delete
echo "Backup verified: $DEST"
