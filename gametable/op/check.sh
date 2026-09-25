#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "CHECK GameTable shell"
bash -n gametable/op/start.sh gametable/op/start-go.sh

echo "CHECK GameTable config"
python3 -m json.tool gametable/opencode.json >/dev/null

echo "CHECK GameTable contract"
python3 -m unittest discover -s gametable/tests -p 'test_*.py' -v

echo "PASS gametable checks"
