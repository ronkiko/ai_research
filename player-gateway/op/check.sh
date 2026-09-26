#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
"$ROOT/player-gateway/op/setup.sh" >/dev/null
cd "$ROOT"
for file in player-gateway/src/*.mjs gametable/web/js/*.js; do
  node --check "$file"
done
cd "$ROOT/player-gateway"
npm test
cd "$ROOT"
python3 -m unittest graphics.tests.test_graphics -v
