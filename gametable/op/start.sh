#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
GAMETABLE_BRAIN_ID="yuki-02"
GAMETABLE_STATE_ROOT="$ROOT/gametable/runtime/$GAMETABLE_BRAIN_ID"
export GAMELAB_BRAIN_STATE_ROOT="$GAMETABLE_STATE_ROOT"
export GAMETABLE_RELATIONSHIP_STATE="$GAMETABLE_STATE_ROOT/relationship-current.json"

ACTION="start"
FRESH=0
case "${1:-}" in
  --start) ACTION="start"; shift ;;
  --fresh) ACTION="start"; FRESH=1; shift ;;
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

if [[ "$FRESH" -eq 1 ]]; then
  op_managed_process \
    stop \
    "gametable-opencode" \
    "$ROOT" \
    "opencode" \
    "$ROOT/gametable" \
    "GameTable OpenCode"
  rm -rf -- "$GAMETABLE_STATE_ROOT"
  echo "GameTable Yuki2 state: fresh"
fi

op_managed_process \
  "$ACTION" \
  "gametable-opencode" \
  "$ROOT" \
  "opencode" \
  "$ROOT/gametable" \
  "GameTable OpenCode" \
  opencode "$@"
