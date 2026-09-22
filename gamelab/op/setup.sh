#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

if [[ "${1:-}" != "--isolated" ]]; then
  if [[ $# -ne 0 ]]; then
    echo "ERROR usage: ./gamelab/op/setup.sh [--isolated]" >&2
    exit 2
  fi
  exec ./gamelab/op/check-env.sh "${GAMELAB_PYTHON:-python3}"
fi

if [[ $# -ne 1 ]]; then
  echo "ERROR usage: ./gamelab/op/setup.sh [--isolated]" >&2
  exit 2
fi

VENV="$ROOT/gamelab/.venv"
PY="$VENV/bin/python"

if [[ ! -x "$PY" ]]; then
  echo "GAMELAB SETUP create isolated environment"
  "${PYTHON:-python3}" -m venv "$VENV"
fi

if ./gamelab/op/check-env.sh "$PY" >/dev/null 2>&1; then
  ./gamelab/op/check-env.sh "$PY"
  exit 0
fi

echo "GAMELAB SETUP install isolated CPU PyTorch"
"$PY" -m pip install --disable-pip-version-check -q   --index-url https://download.pytorch.org/whl/cpu   --extra-index-url https://pypi.org/simple   "torch>=2.1,<3"

echo "GAMELAB SETUP install isolated Python dependencies"
"$PY" -m pip install --disable-pip-version-check -q   "mcp==2.2.0" "numpy>=1.26,<3"

./gamelab/op/check-env.sh "$PY"
