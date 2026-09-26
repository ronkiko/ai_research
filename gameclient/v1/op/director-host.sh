#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
ACTION="start"
case "${1:-}" in
  --start) ACTION="start"; shift ;;
  --stop) ACTION="stop"; shift ;;
  --restart) ACTION="restart"; shift ;;
  --status) ACTION="status"; shift ;;
esac
if [[ "$ACTION" == "stop" || "$ACTION" == "status" ]] && [[ $# -ne 0 ]]; then
  echo "ERROR $ACTION does not accept application arguments" >&2
  exit 2
fi
source "$ROOT/op/process.sh"
GATE="${DIRECTOR_MANUAL_GATE:-$ROOT/gametable/runtime/director-manual.json}"
TAG="gameclient-director-host-v1"
MARKER="gameclient.v1.host.server --port 17701"
LABEL="Director Host v1"
PORT=17701

port_open() {
  "${PYTHON:-python3}" - "$PORT" <<'PY' >/dev/null 2>&1
import socket
import sys
port = int(sys.argv[1])
try:
    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
        pass
except OSError:
    raise SystemExit(1)
raise SystemExit(0)
PY
}

if [[ "$ACTION" == "restart" ]]; then
  op_managed_process stop "$TAG" "$ROOT" "$MARKER" "$ROOT" "$LABEL"
  ACTION="start"
fi

if [[ "$ACTION" == "start" ]]; then
  if ! op_locate_process "$TAG" "$ROOT" "$MARKER" "$ROOT" >/dev/null 2>&1 && port_open; then
    echo "ERROR $LABEL port $PORT is occupied by an unmanaged or foreign process" >&2
    echo "Refusing to start over it; inspect the port owner instead of reusing stale Host state." >&2
    exit 2
  fi
fi

op_managed_process \
  "$ACTION" \
  "$TAG" \
  "$ROOT" \
  "$MARKER" \
  "$ROOT" \
  "$LABEL" \
  "${PYTHON:-python3}" -m gameclient.v1.host.server \
    --port 17701 --manual-gate "$GATE" "$@"
