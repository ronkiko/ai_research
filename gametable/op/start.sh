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
  echo "ERROR $ACTION does not accept OpenCode arguments" >&2
  exit 2
fi

if [[ "$ACTION" == "start" || "$ACTION" == "restart" ]]; then
  if ! command -v opencode >/dev/null 2>&1; then
    echo "ERROR opencode is not installed or not on PATH" >&2
    exit 2
  fi
fi

# shellcheck source=/dev/null
source "$ROOT/op/process.sh"
op_managed_process \
  "$ACTION" \
  "gametable-opencode" \
  "$ROOT" \
  "opencode" \
  "$ROOT/gametable" \
  "GameTable OpenCode" \
  opencode "$@"
