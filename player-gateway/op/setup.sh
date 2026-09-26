#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
command -v node >/dev/null || { echo "ERROR node is not installed" >&2; exit 2; }
command -v npm >/dev/null || { echo "ERROR npm is not installed" >&2; exit 2; }
cd "$ROOT/player-gateway"
npm install --ignore-scripts --no-audit --no-fund
