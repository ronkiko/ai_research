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
op_managed_process \
  "$ACTION" \
  "gameclient-director-gui-v1" \
  "$ROOT" \
  "gameclient.v1.clients.gui" \
  "$ROOT" \
  "Director GUI v1" \
  "${PYTHON:-python3}" -m gameclient.v1.clients.gui \
    --port 17701 --player director1 "$@"
