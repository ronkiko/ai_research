#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
ACTION="start"
FRESH=0
case "${1:-}" in
  --start) shift ;;
  --fresh) FRESH=1; shift ;;
  --stop) ACTION="stop"; shift ;;
  --restart) ACTION="restart"; shift ;;
  --status) ACTION="status"; shift ;;
esac
if [[ "$ACTION" == "stop" || "$ACTION" == "status" ]] && [[ $# -ne 0 ]]; then
  echo "ERROR $ACTION does not accept arguments" >&2
  exit 2
fi
source "$ROOT/op/process.sh"
if [[ "$FRESH" -eq 1 ]]; then
  op_managed_process stop "gametable-vn" "$ROOT" "roleplay.server" "$ROOT/gametable" "GameTable Юки"
  # Fresh means only GameTable's current VN save; never laboratory/model artifacts.
  rm -rf -- "$ROOT/gametable/runtime/yuki-vn"
  echo "GameTable Юки: новое знакомство"
fi
if [[ "$ACTION" == "start" || "$ACTION" == "restart" ]]; then
  command -v opencode >/dev/null || { echo "ERROR opencode is not installed" >&2; exit 2; }
  command -v python3 >/dev/null || { echo "ERROR python3 is not installed" >&2; exit 2; }
fi
# Isolate any downstream lab bookkeeping from the retired Yuki2 experiment.
export GAMELAB_BRAIN_STATE_ROOT="$ROOT/gametable/runtime/yuki-vn/laboratory"
op_managed_process "$ACTION" "gametable-vn" "$ROOT" "roleplay.server" \
  "$ROOT/gametable" "GameTable Юки" python3 -m roleplay.server "$@"
