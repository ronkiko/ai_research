#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PY="${GAMELAB_PYTHON:-python3}"
exec "$PY" -m gamelab.mcp "$@"
