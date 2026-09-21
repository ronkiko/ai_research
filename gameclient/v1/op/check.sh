#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"

echo "CHECK compileall"
python3 -m compileall -q gameserver/v1 gameclient/v1

echo "CHECK GameServer tests"
python3 -m unittest discover -s gameserver/v1/tests -v

echo "CHECK GameClient tests"
python3 -m unittest discover -s gameclient/v1/tests -v

echo "CHECK real Server -> Host -> CLI smoke"
python3 gameclient/v1/tests/smoke_runtime.py

echo "CHECK isolated MCP SDK"
./gameclient/v1/op/mcp-setup.sh

echo "CHECK real Server -> Host -> MCP stdio smoke"
gameclient/v1/.venv-mcp/bin/python gameclient/v1/tests/smoke_mcp.py

echo "PASS game v1 checks"
