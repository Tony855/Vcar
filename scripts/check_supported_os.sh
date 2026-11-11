#!/usr/bin/env bash
set -Eeuo pipefail

OS_RELEASE_FILE="${1:-/etc/os-release}"
if [[ ! -r "$OS_RELEASE_FILE" ]]; then
  echo "Cannot read OS release metadata: $OS_RELEASE_FILE" >&2
  exit 1
fi

# shellcheck disable=SC1090
. "$OS_RELEASE_FILE"

version_at_least() {
  local actual="$1" minimum="$2"
  [[ "$(printf '%s\\n' "$actual" "$minimum" | sort -V | head -n1)" == "$minimum" ]]
}

case "${ID:-}" in
  ubuntu)
    if ! version_at_least "${VERSION_ID:-0}" "24.04"; then
      echo "Ubuntu 24.04 or newer is required (detected ${VERSION_ID:-unknown})." >&2
      exit 1
    fi
    ;;
  debian)
    if ! version_at_least "${VERSION_ID:-0}" "13"; then
      echo "Debian 13 or newer is required (detected ${VERSION_ID:-unknown})." >&2
      exit 1
    fi
    ;;
  *)
    echo "Unsupported Linux distribution: ${ID:-unknown}. Use Ubuntu 24.04+ or Debian 13+." >&2
    exit 1
    ;;
esac

echo "Supported operating system: ${PRETTY_NAME:-$ID $VERSION_ID}"
