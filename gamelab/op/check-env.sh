#!/usr/bin/env bash
set -euo pipefail

PY="${1:-${GAMELAB_PYTHON:-python3}}"

if ! command -v "$PY" >/dev/null 2>&1 && [[ ! -x "$PY" ]]; then
  echo "ERROR GameLab Python not found: $PY" >&2
  exit 2
fi

"$PY" - <<'PY'
from __future__ import annotations

import importlib.metadata
import sys


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
            raise ValueError(raw)
        values.append(int(digits))
    if len(values) != parts:
        raise ValueError(raw)
    return tuple(values)


errors: list[str] = []

try:
    import torch
except Exception as exc:
    errors.append(f"torch unavailable: {exc}")
else:
    try:
        torch_version = version_tuple(torch.__version__)
    except ValueError:
        errors.append(f"cannot parse torch version {torch.__version__!r}")
    else:
        if not (torch_version >= (2, 1) and torch_version < (3, 0)):
            errors.append(f"torch must be >=2.1,<3; found {torch.__version__}")

try:
    import numpy
except Exception as exc:
    errors.append(f"numpy unavailable: {exc}")
else:
    try:
        numpy_version = version_tuple(numpy.__version__)
    except ValueError:
        errors.append(f"cannot parse numpy version {numpy.__version__!r}")
    else:
        if not (numpy_version >= (1, 26) and numpy_version < (3, 0)):
            errors.append(f"numpy must be >=1.26,<3; found {numpy.__version__}")

try:
    mcp_version = importlib.metadata.version("mcp")
except importlib.metadata.PackageNotFoundError:
    errors.append("mcp unavailable")
else:
    if mcp_version != "2.2.0":
        errors.append(f"mcp must be exactly 2.2.0; found {mcp_version}")

if errors:
    for error in errors:
        print(f"ERROR {error}", file=sys.stderr)
    print(
        "ERROR install compatible dependencies or run "
        "./gamelab/op/setup.sh --isolated",
        file=sys.stderr,
    )
    raise SystemExit(2)

print(
    "GAMELAB ENV ready "
    f"python={sys.executable} "
    f"torch={torch.__version__} "
    f"numpy={numpy.__version__} "
    f"mcp={mcp_version}"
)
PY
