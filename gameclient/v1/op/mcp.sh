#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
VENV="$ROOT/gameclient/v1/.venv-mcp"
PYTHON_BIN="$VENV/bin/python"

cd "$ROOT"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "ERROR isolated MCP environment is missing." >&2
  echo "Run: ./gameclient/v1/op/mcp-setup.sh" >&2
  exit 2
fi

if ! "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import importlib.metadata
raise SystemExit(0 if importlib.metadata.version("mcp") == "2.2.0" else 1)
PY
then
  echo "ERROR MCP environment must contain exactly mcp==2.2.0." >&2
  echo "Run: ./gameclient/v1/op/mcp-setup.sh" >&2
  exit 2
fi

export PYTHONUNBUFFERED=1
exec "$PYTHON_BIN" -m gameclient.v1.clients.mcp "$@"
