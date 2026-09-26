#!/usr/bin/env bash
# Private Organism runtime bootstrap. Public operator scripts source this file.
set -euo pipefail

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  echo "ERROR _env.sh is internal; use ./organism/op/organism.sh" >&2
  exit 2
fi

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
ORGANISM_VENV="$ROOT/organism/.venv"
ORGANISM_PY="$ORGANISM_VENV/bin/python"

_python_ok() {
  [[ -x "$ORGANISM_PY" ]] || return 1
  "$ORGANISM_PY" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
}

_env_ok() {
  _python_ok || return 1
  (
    cd "$ROOT"
    "$ORGANISM_PY" - <<'PY' >/dev/null 2>&1
import importlib.metadata

def version_tuple(raw: str, parts: int = 2):
    values = []
    for item in raw.split("+", 1)[0].split(".")[:parts]:
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

torch_version = version_tuple(importlib.metadata.version("torch"))
numpy_version = version_tuple(importlib.metadata.version("numpy"))
mcp_version = importlib.metadata.version("mcp")
raise SystemExit(
    0 if (2, 1) <= torch_version < (3, 0)
    and (1, 26) <= numpy_version < (3, 0)
    and mcp_version == "2.2.0"
    else 1
)
PY
  )
}

BOOTSTRAP_PY="$(command -v python3 || true)"
[[ -n "$BOOTSTRAP_PY" ]] || {
  echo "ERROR Organism requires python3" >&2
  exit 2
}

if [[ -e "$ORGANISM_VENV" ]] && ! _python_ok; then
  echo "ORGANISM ENV rebuild incompatible private runtime" >&2
  rm -rf "$ORGANISM_VENV"
fi

if [[ ! -x "$ORGANISM_PY" ]]; then
  echo "ORGANISM ENV create private runtime" >&2
  "$BOOTSTRAP_PY" -m venv "$ORGANISM_VENV"
fi

if ! _env_ok; then
  echo "ORGANISM ENV synchronize dependencies" >&2
  "$ORGANISM_PY" -m pip install --disable-pip-version-check -q --upgrade     --index-url https://download.pytorch.org/whl/cpu     --extra-index-url https://pypi.org/simple     "torch>=2.1,<3" "numpy>=1.26,<3" "mcp==2.2.0" 1>&2
fi

_env_ok || { echo "ERROR Organism private runtime is not usable" >&2; exit 2; }
export PYTHONNOUSERSITE=1
readonly ROOT ORGANISM_VENV ORGANISM_PY
