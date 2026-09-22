#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT/gametable"

if ! command -v opencode >/dev/null 2>&1; then
  echo "ERROR opencode is not installed or not on PATH" >&2
  exit 2
fi

exec opencode "$@"
