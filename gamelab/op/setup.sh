#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV="$ROOT/gamelab/.venv"
PY="$VENV/bin/python"
cd "$ROOT"

if [[ ! -x "$PY" ]]; then
  echo "GAMELAB SETUP create isolated environment"
  "${PYTHON:-python3}" -m venv "$VENV"
fi

if "$PY" - <<'PY' >/dev/null 2>&1
import importlib.metadata
import numpy
import torch

parts = torch.__version__.split("+", 1)[0].split(".")
version = tuple(int(part) for part in parts[:2])
mcp_version = importlib.metadata.version("mcp")
raise SystemExit(0 if (2, 1) <= version < (3, 0) and mcp_version == "2.2.0" else 1)
PY
then
  echo "GAMELAB SETUP ready"
  exit 0
fi

echo "GAMELAB SETUP install CPU PyTorch"
"$PY" -m pip install --disable-pip-version-check -q   --index-url https://download.pytorch.org/whl/cpu   --extra-index-url https://pypi.org/simple   "torch>=2.1,<3"

echo "GAMELAB SETUP install Python dependencies"
"$PY" -m pip install --disable-pip-version-check -q "mcp==2.2.0" "numpy>=1.26,<3"

"$PY" - <<'PY'
import importlib.metadata
import numpy
import torch
print(
    f"GAMELAB SETUP ready torch={torch.__version__} "
    f"numpy={numpy.__version__} mcp={importlib.metadata.version('mcp')}"
)
PY
