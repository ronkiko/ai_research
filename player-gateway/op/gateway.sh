#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
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
if [[ "$ACTION" == "start" || "$ACTION" == "restart" ]]; then
  command -v node >/dev/null || { echo "ERROR node is not installed" >&2; exit 2; }
  [[ -f "$ROOT/player-gateway/node_modules/socket.io/package.json" ]] || {
    echo "ERROR Player Gateway dependencies are missing; run ./player-gateway/op/setup.sh" >&2
    exit 2
  }
fi
op_managed_process   "$ACTION"   "player-gateway"   "$ROOT"   "player-gateway/src/server.mjs"   "$ROOT"   "Player Gateway"   node "$ROOT/player-gateway/src/server.mjs" "$@"
