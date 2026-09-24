#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$ROOT/gamelab/op/_env.sh"
cd "$ROOT"
exec "$GAMELAB_PY" -m gamelab.mcp "$@"
