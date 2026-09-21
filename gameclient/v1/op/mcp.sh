#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"
if ! "${PYTHON:-python3}" -c 'import mcp' >/dev/null 2>&1; then
  echo "ERROR MCP Python SDK v2 is required: python -m pip install 'mcp>=2,<3'" >&2
  exit 2
fi
exec "${PYTHON:-python3}" -m gameclient.v1.clients.mcp "$@"
