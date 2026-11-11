#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 /path/to/frontend/dist /path/to/app/web/static" >&2
  exit 2
fi

SOURCE_DIR="$(cd "$1" && pwd)"
TARGET="$2"
if [[ ! -f "$SOURCE_DIR/index.html" ]]; then
  echo "Frontend build is incomplete: index.html is missing from $SOURCE_DIR" >&2
  exit 1
fi

PARENT="$(dirname "$TARGET")"
NAME="$(basename "$TARGET")"
install -d "$PARENT"
STAGING="$PARENT/.${NAME}.new.$$"
PREVIOUS="$PARENT/.${NAME}.previous.$$"
cleanup() {
  rm -rf "$STAGING"
  if [[ -d "$PREVIOUS" && ! -e "$TARGET" ]]; then
    mv "$PREVIOUS" "$TARGET"
  fi
  rm -rf "$PREVIOUS"
}
trap cleanup EXIT

rm -rf "$STAGING" "$PREVIOUS"
install -d "$STAGING"
cp -a "$SOURCE_DIR/." "$STAGING/"

if [[ -e "$TARGET" || -L "$TARGET" ]]; then
  mv "$TARGET" "$PREVIOUS"
fi
if mv "$STAGING" "$TARGET"; then
  rm -rf "$PREVIOUS"
else
  echo "Unable to publish frontend build; restoring previous assets." >&2
  if [[ -e "$PREVIOUS" && ! -e "$TARGET" ]]; then
    mv "$PREVIOUS" "$TARGET"
  fi
  exit 1
fi
trap - EXIT
echo "Frontend assets published: $TARGET"
