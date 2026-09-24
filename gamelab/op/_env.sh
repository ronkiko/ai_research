#!/usr/bin/env bash
# Private GameLab runtime bootstrap. Public operator scripts source this file.
set -euo pipefail

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  echo "ERROR _env.sh is internal; use ./gamelab/op/gamelab.sh" >&2
  exit 2
fi

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
GAMELAB_VENV="$ROOT/gamelab/.venv"
GAMELAB_PY="$GAMELAB_VENV/bin/python"

_gamelab_python_ok() {
  [[ -x "$GAMELAB_PY" ]] || return 1
  "$GAMELAB_PY" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
}

_gamelab_env_ok() {
  _gamelab_python_ok || return 1
  "$GAMELAB_PY" - <<'PY' >/dev/null 2>&1
from __future__ import annotations

import importlib.metadata


def version_tuple(raw: str, parts: int = 2) -> tuple[int, ...]:
    base = raw.split("+", 1)[0]
    values: list[int] = []
    for item in base.split(".")[:parts]:
        digits = ""
        for char in item:
            if char.isdigit():
                digits += char
            else:
                break
        if not digits:
            raise SystemExit(1)
        values.append(int(digits))
    if len(values) != parts:
        raise SystemExit(1)
    return tuple(values)


import torch
import numpy

torch_version = version_tuple(torch.__version__)
numpy_version = version_tuple(numpy.__version__)
mcp_version = importlib.metadata.version("mcp")

if not ((2, 1) <= torch_version < (3, 0)):
    raise SystemExit(1)
if not ((1, 26) <= numpy_version < (3, 0)):
    raise SystemExit(1)
if mcp_version != "2.2.0":
    raise SystemExit(1)
PY
}

BOOTSTRAP_PY="$(command -v python3 || true)"
if [[ -z "$BOOTSTRAP_PY" ]]; then
  echo "ERROR GameLab requires python3 to bootstrap its private runtime" >&2
  exit 2
fi

if [[ -e "$GAMELAB_VENV" ]] && ! _gamelab_python_ok; then
  echo "GAMELAB ENV rebuild incompatible private runtime" >&2
  rm -rf "$GAMELAB_VENV"
fi

if [[ ! -x "$GAMELAB_PY" ]]; then
  echo "GAMELAB ENV create private runtime" >&2
  "$BOOTSTRAP_PY" -m venv "$GAMELAB_VENV"
fi

if ! _gamelab_env_ok; then
  echo "GAMELAB ENV synchronize dependencies" >&2
  "$GAMELAB_PY" -m pip install --disable-pip-version-check -q \
    --index-url https://download.pytorch.org/whl/cpu \
    --extra-index-url https://pypi.org/simple \
    "torch>=2.1,<3" "numpy>=1.26,<3" "mcp==2.2.0" 1>&2
fi

if ! _gamelab_env_ok; then
  echo "ERROR GameLab private runtime is not usable" >&2
  exit 2
fi

export PYTHONNOUSERSITE=1
readonly ROOT GAMELAB_VENV GAMELAB_PY
