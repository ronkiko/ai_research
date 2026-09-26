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
# shellcheck source=/dev/null
source "$ROOT/op/process.sh"
TAG="gameclient-host-v1"
MARKER="gameclient.v1.host.server --port 17700"
LABEL="GameClient Host v1"
PORT=17700

if [[ "$ACTION" == "restart" ]]; then
  op_managed_process stop "$TAG" "$ROOT" "$MARKER" "$ROOT" "$LABEL"
  ACTION="start"
fi

if [[ "$ACTION" == "start" ]]; then
  op_require_tcp_port_free "127.0.0.1" "$PORT" "$LABEL"
fi

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
  "${PYTHON:-python3}" -m gameclient.v1.host.server --port 17700 "$@"
