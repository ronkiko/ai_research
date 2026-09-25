#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
echo "CHECK GameTable syntax"
bash -n gametable/op/start.sh gametable/op/start-go.sh
for file in gametable/web/js/*.js; do
  node --check "$file"
done
python3 -m json.tool gametable/opencode.json >/dev/null
python3 -m json.tool gametable/roleplay/rules.json >/dev/null
echo "CHECK GameTable runtime"
python3 -m unittest discover -s gametable/tests -p 'test_*.py' -v
if [[ "${1:-}" == "--live" ]]; then
  echo "CHECK GameTable live OpenCode (temporary save)"
  python3 -m gametable.tests.live_opencode
elif [[ $# -ne 0 ]]; then
  echo "ERROR usage: check.sh [--live]" >&2
  exit 2
fi
echo "PASS gametable checks"
