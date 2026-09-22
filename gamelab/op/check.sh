#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

./gamelab/op/setup.sh
PY="$ROOT/gamelab/.venv/bin/python"

echo "CHECK GameLab compile"
"$PY" -m compileall -q -x '/\.venv/' gamelab

echo "CHECK GameLab unit tests"
"$PY" -m unittest discover -s gamelab/tests -p 'test_*.py' -v

echo "CHECK real GameLab model -> Host -> GameServer smoke"
"$PY" gamelab/tests/smoke_runtime.py

echo "CHECK real OpenCode-facing MCP goal smoke"
"$PY" gamelab/tests/smoke_mcp.py

echo "PASS gamelab checks"
