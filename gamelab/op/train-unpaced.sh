#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PY="${GAMELAB_PYTHON:-python3}"
./gamelab/op/check-env.sh "$PY" >/dev/null
exec "$PY" -m gamelab.training --mode unpaced "$@"
