#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON:-python3}"

if [[ $# -ge 1 && "$1" =~ ^[0-9]+$ ]]; then
  screen="$1"
  shift
  exec "$PYTHON_BIN" -m game2.v2.management.screen_client --screen "$screen" "$@"
fi

exec "$PYTHON_BIN" -m game2.v2.management.screen_control "$@"
