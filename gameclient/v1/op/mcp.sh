#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
VENV="$ROOT/gameclient/v1/.venv-mcp"
PYTHON_BIN="$VENV/bin/python"

cd "$ROOT"

mcp_env_ready() {
  [[ -x "$PYTHON_BIN" ]] || return 1
  "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import importlib.metadata
raise SystemExit(0 if importlib.metadata.version("mcp") == "2.2.0" else 1)
PY
}

if ! mcp_env_ready; then
  echo "GAMECLIENT MCP prepare private runtime" >&2
  "$ROOT/gameclient/v1/op/mcp-setup.sh" >&2
fi

if ! mcp_env_ready; then
  echo "ERROR GameClient MCP private runtime is not usable" >&2
  exit 2
fi

export PYTHONUNBUFFERED=1
exec "$PYTHON_BIN" -m gameclient.v1.clients.mcp "$@"
