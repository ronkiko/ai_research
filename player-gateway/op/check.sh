#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
"$ROOT/player-gateway/op/setup.sh" >/dev/null
cd "$ROOT"
node --check player-gateway/src/server.mjs
node --check player-gateway/public/app.js
cd "$ROOT/player-gateway"
npm test
cd "$ROOT"
python3 -m unittest graphics.tests.test_graphics -v
