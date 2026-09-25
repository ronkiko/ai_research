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
echo "PASS embodied world contracts"
