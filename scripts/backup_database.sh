#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${VCAR_ENV_FILE:-$ROOT_DIR/.env}"
BACKUP_DIR="${VCAR_BACKUP_DIR:-/var/backups/vcar}"
METRICS_FILE="${VCAR_BACKUP_METRICS_FILE:-}"
BACKUP_SUCCEEDED=0
TMP=""

write_backup_metrics() {
  [[ -n "$METRICS_FILE" ]] || return 0
  local metrics_dir previous_success metrics_tmp
  metrics_dir="$(dirname "$METRICS_FILE")"
  if [[ ! -d "$metrics_dir" ]]; then
    echo "Backup metrics directory does not exist: $metrics_dir" >&2
    return 1
  fi
  previous_success=0
  if [[ -r "$METRICS_FILE" ]]; then
    previous_success="$(awk '$1 == "vcar_backup_last_success_timestamp_seconds" {print $2; exit}' "$METRICS_FILE")"
    [[ "$previous_success" =~ ^[0-9]+([.][0-9]+)?$ ]] || previous_success=0
  fi
  if [[ "$BACKUP_SUCCEEDED" -eq 1 ]]; then
    previous_success="$(date +%s)"
  fi
  metrics_tmp="$(mktemp "${METRICS_FILE}.XXXXXX")" || return 1
  if ! {
    printf '# HELP vcar_backup_last_success_timestamp_seconds Unix timestamp of the last verified database backup.\n'
    printf '# TYPE vcar_backup_last_success_timestamp_seconds gauge\n'
    printf 'vcar_backup_last_success_timestamp_seconds %s\n' "$previous_success"
    printf '# HELP vcar_backup_last_run_success Whether the most recent backup run completed and verified.\n'
    printf '# TYPE vcar_backup_last_run_success gauge\n'
    printf 'vcar_backup_last_run_success %s\n' "$BACKUP_SUCCEEDED"
  } > "$metrics_tmp" || ! chmod 644 "$metrics_tmp" || ! mv -f "$metrics_tmp" "$METRICS_FILE"; then
    rm -f "$metrics_tmp"
    return 1
  fi
}

finish_backup() {
  local status=$?
  [[ -z "$TMP" ]] || rm -f "$TMP"
  if ! write_backup_metrics; then
    echo "Warning: could not update backup metrics textfile." >&2
  fi
  exit "$status"
}
trap finish_backup EXIT

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
# install -d does not change permissions when the directory already exists.
chmod 700 "$BACKUP_DIR"
umask 077
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
# mktemp makes concurrent backups unique even when both start in the same second.
TMP="$(mktemp "$BACKUP_DIR/.vcar-$STAMP.XXXXXX.dump.tmp")"
UNIQUE="$(basename "$TMP")"
UNIQUE="${UNIQUE#.vcar-$STAMP.}"
UNIQUE="${UNIQUE%.dump.tmp}"
DEST="$BACKUP_DIR/vcar-$STAMP-$UNIQUE.dump"

pg_dump --format=custom --no-owner --no-acl --file="$TMP" "$BACKUP_DATABASE_URL"
pg_restore --list "$TMP" >/dev/null
mv "$TMP" "$DEST"
chmod 600 "$DEST"
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'vcar-*.dump' -mtime +14 -delete
BACKUP_SUCCEEDED=1
echo "Backup verified: $DEST"
