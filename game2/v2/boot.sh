#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
exec "${PYTHON:-python3}" -m game2.v2.console.main --server \
  --config "$ROOT/game2/v2/console/configs/server.json" "$@"
