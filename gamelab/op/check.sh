#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
if [[ "${1:-}" == "--existing-server" ]]; then
  export GAMELAB_TEST_EXISTING_SERVER=1
elif [[ $# -ne 0 ]]; then
  echo "usage: $0 [--existing-server]" >&2
  exit 2
fi

PY="${GAMELAB_PYTHON:-python3}"
./gamelab/op/check-env.sh "$PY"

echo "CHECK GameLab compile"
"$PY" -m compileall -q -x '/\.venv/' gamelab

echo "CHECK GameLab unit tests"
"$PY" -m unittest discover -s gamelab/tests -p 'test_*.py' -v

echo "CHECK real GameLab model -> Host -> GameServer smoke"
"$PY" -m gamelab.tests.smoke_runtime

echo "CHECK fresh learned Motor + Spine convergence and paced frozen verification"
"$PY" -m gamelab.tests.convergence_spine

echo "CHECK real OpenCode-facing MCP goal smoke"
"$PY" -m gamelab.tests.smoke_mcp

echo "PASS gamelab checks"
