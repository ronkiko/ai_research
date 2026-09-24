#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$ROOT/gamelab/op/_env.sh"
cd "$ROOT"

if [[ "${1:-}" == "--existing-server" ]]; then
  export GAMELAB_TEST_EXISTING_SERVER=1
elif [[ $# -ne 0 ]]; then
  echo "usage: $0 [--existing-server]" >&2
  exit 2
fi

echo "CHECK GameLab compile"
"$GAMELAB_PY" -m compileall -q -x '/\.venv/' gamelab

echo "CHECK GameLab unit tests"
"$GAMELAB_PY" -m unittest discover -s gamelab/tests -p 'test_*.py' -v

echo "CHECK real GameLab model -> Host -> GameServer smoke"
"$GAMELAB_PY" -m gamelab.tests.smoke_runtime

echo "CHECK real OpenCode-facing MCP goal smoke"
"$GAMELAB_PY" -m gamelab.tests.smoke_mcp

echo "PASS gamelab checks"
