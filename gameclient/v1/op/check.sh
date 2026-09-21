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

echo "PASS game v1 checks"
