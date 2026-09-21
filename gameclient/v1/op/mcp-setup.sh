#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
VENV="$ROOT/gameclient/v1/.venv-mcp"
PYTHON_BIN="${PYTHON:-python3}"

cd "$ROOT"

if [[ ! -x "$VENV/bin/python" ]]; then
  echo "MCP SETUP create isolated environment"
  "$PYTHON_BIN" -m venv "$VENV"
fi

if "$VENV/bin/python" - <<'PY' >/dev/null 2>&1
import importlib.metadata
raise SystemExit(0 if importlib.metadata.version("mcp") == "2.2.0" else 1)
PY
then
  echo "MCP SETUP ready mcp=2.2.0"
  exit 0
fi

echo "MCP SETUP install pinned SDK mcp=2.2.0"
"$VENV/bin/python" -m pip install --disable-pip-version-check -q   -r gameclient/v1/requirements-mcp.txt

"$VENV/bin/python" - <<'PY'
import importlib.metadata
version = importlib.metadata.version("mcp")
if version != "2.2.0":
    raise SystemExit(f"unexpected mcp version: {version}")
print(f"MCP SETUP ready mcp={version}")
PY
