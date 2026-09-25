#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
echo "CHECK embodied world manifests"
for file in world/maps/*.json; do
  python3 -m json.tool "$file" >/dev/null
done
echo "CHECK embodied world contracts"
python3 -m unittest discover -s world/tests -p 'test_*.py' -v
echo "CHECK embodied GameServer physics mode"
python3 -m unittest gameserver.v1.tests.test_embodied_world -v
echo "CHECK legacy GameTable remains valid before cutover"
./gametable/op/check.sh
echo "PASS embodied world contracts"
