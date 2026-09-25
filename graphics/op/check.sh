#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
echo "CHECK graphics Python"
python3 -m compileall -q graphics
echo "CHECK graphics contracts"
python3 -m unittest discover -s graphics/tests -p 'test_*.py' -v
echo "CHECK browser graphics modules"
node --check gametable/web/js/frame-renderer.js
node --check gametable/web/js/frames.js
echo "PASS graphics checks"
