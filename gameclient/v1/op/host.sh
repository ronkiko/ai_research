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
LABEL="GameClient Host v1"

if [[ "$ACTION" == "restart" ]]; then
  op_managed_process stop "$TAG" "$ROOT" "$ROOT" "$LABEL"
  ACTION="start"
fi
if [[ "$ACTION" == "start" ]]; then
  op_require_tcp_port_free "127.0.0.1" 17700 "$LABEL"
fi

op_managed_process \
  "$ACTION" \
  "$TAG" \
  "$ROOT" \
  "$ROOT" \
  "$LABEL" \
  "${PYTHON:-python3}" -m gameclient.v1.host.server --port 17700 "$@"
