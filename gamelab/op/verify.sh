#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="$ROOT/gamelab/.venv/bin/python"
cd "$ROOT"
if [[ ! -x "$PY" ]]; then
  echo "ERROR run ./gamelab/op/setup.sh first" >&2
  exit 2
fi
exec "$PY" -m gamelab.verify "$@"
