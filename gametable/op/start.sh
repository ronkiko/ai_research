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

source "$ROOT/op/process.sh"

stop_stack() {
  "$ROOT/player-gateway/op/gateway.sh" --stop || true
  op_managed_process stop "gametable-vn" "$ROOT" "roleplay.server"     "$ROOT/gametable" "GameTable Юки" || true
  "$ROOT/gameclient/v1/op/director-host.sh" --stop || true
  "$ROOT/gameclient/v1/op/host.sh" --stop || true
  "$ROOT/gameserver/v1/op/embodied.sh" --stop || true
}

status_stack() {
  "$ROOT/player-gateway/op/gateway.sh" --status || true
  op_managed_process status "gametable-vn" "$ROOT" "roleplay.server"     "$ROOT/gametable" "GameTable Юки" || true
  "$ROOT/gameclient/v1/op/host.sh" --status || true
  "$ROOT/gameclient/v1/op/director-host.sh" --status || true
  "$ROOT/gameserver/v1/op/embodied.sh" --status || true
}

if [[ "$ACTION" == "stop" ]]; then
  [[ $# -eq 0 ]] || { echo "ERROR stop does not accept arguments" >&2; exit 2; }
  stop_stack
  exit 0
fi
if [[ "$ACTION" == "status" ]]; then
  [[ $# -eq 0 ]] || { echo "ERROR status does not accept arguments" >&2; exit 2; }
  status_stack
  exit 0
fi
if [[ "$ACTION" == "restart" ]]; then
  stop_stack
fi

command -v opencode >/dev/null || { echo "ERROR opencode is not installed" >&2; exit 2; }
command -v python3 >/dev/null || { echo "ERROR python3 is not installed" >&2; exit 2; }
command -v node >/dev/null || { echo "ERROR node is not installed" >&2; exit 2; }
command -v npm >/dev/null || { echo "ERROR npm is not installed" >&2; exit 2; }
cd "$ROOT"

if op_managed_process status "gametable-vn" "$ROOT" "roleplay.server" \
  "$ROOT/gametable" "GameTable Юки" >/dev/null 2>&1; then
  echo "GameTable Юки is already running; use --status or --restart"
  exit 0
fi

echo "CUTOVER inventory"
python3 -m gametable.migration dry-run
echo "CUTOVER migrate"
python3 -m gametable.migration migrate >/dev/null
eval "$(python3 -m gametable.migration env)"
export DIRECTOR_MANUAL_GATE="${DIRECTOR_MANUAL_GATE:-$ROOT/gametable/runtime/director-manual.json}"

if [[ "$FRESH" -eq 1 ]]; then
  # A fresh story must really satisfy the first-day contract P@0 / D@1.
  # Keep learned artifacts, but stop only launcher-owned runtime processes and
  # discard the active physical checkpoint before the new Hosts log in.
  stop_stack
  if python3 - <<'PY' >/dev/null 2>&1
import socket
from gameserver.v1.common.config import GATEWAY_PORT, HOST
with socket.create_connection((HOST, GATEWAY_PORT), timeout=.2):
    pass
PY
  then
    echo "ERROR --fresh cannot reset an unmanaged service on the embodied Gateway port" >&2
    exit 2
  fi
  python3 - <<'PY'
from gametable.migration import WORLD_STATE, active_save_root
root = active_save_root()
for name in ("save.sqlite3", "save.sqlite3-wal", "save.sqlite3-shm"):
    (root / name).unlink(missing_ok=True)
for path in (
    WORLD_STATE,
    WORLD_STATE.with_name(WORLD_STATE.name + "-wal"),
    WORLD_STATE.with_name(WORLD_STATE.name + "-shm"),
):
    path.unlink(missing_ok=True)
print("GameTable Юки: новое знакомство; навыки сохранены; тело начнёт день у EXIT")
PY
  # --fresh is a new first day, not the migrated legacy placement.
  export EMBODIED_INITIAL_ZONE="hallway"
  export EMBODIED_INITIAL_SPAWN="yuki_day_start"
fi

LOGDIR="$ROOT/gametable/runtime/stack-logs"
mkdir -p "$LOGDIR"

if [[ ! -f "$ROOT/player-gateway/node_modules/socket.io/package.json" ]]; then
  echo "CUTOVER prepare Player Gateway"
  "$ROOT/player-gateway/op/setup.sh" >"$LOGDIR/player-gateway-setup.log" 2>&1
fi

GAMETABLE_PORT=17880
argv=("$@")
for ((i=0; i<${#argv[@]}; i++)); do
  case "${argv[$i]}" in
    --port)
      (( i + 1 < ${#argv[@]} )) || { echo "ERROR --port requires a value" >&2; exit 2; }
      GAMETABLE_PORT="${argv[$((i+1))]}"
      ;;
    --port=*)
      GAMETABLE_PORT="${argv[$i]#--port=}"
      ;;
  esac
done
export PLAYER_GATEWAY_GAMETABLE_PORT="${PLAYER_GATEWAY_GAMETABLE_PORT:-$GAMETABLE_PORT}"

gateway_is_embodied() {
  python3 - <<'PY' >/dev/null 2>&1
from gameserver.v1.common.config import GATEWAY_PORT, HOST
from gameserver.v1.common.protocol import message, rpc
value = rpc(HOST, GATEWAY_PORT, message("health"), timeout=.2)
raise SystemExit(0 if value.get("mode") == "embodied_world_v1" else 1)
PY
}

if ! "$ROOT/gameserver/v1/op/embodied.sh" --status >/dev/null 2>&1; then
  if ! gateway_is_embodied; then
    if "$ROOT/gameserver/v1/op/server.sh" --status >/dev/null 2>&1; then
      echo "CUTOVER stop legacy GameServer"
      "$ROOT/gameclient/v1/op/host.sh" --stop || true
      "$ROOT/gameserver/v1/op/server.sh" --stop
    elif python3 - <<'PY' >/dev/null 2>&1
import socket
from gameserver.v1.common.config import GATEWAY_PORT, HOST
with socket.create_connection((HOST, GATEWAY_PORT), timeout=.2):
    pass
PY
    then
      echo "ERROR port 17600 is occupied by an incompatible unmanaged service" >&2
      exit 2
    fi
    nohup "$ROOT/gameserver/v1/op/embodied.sh" --start \
      >"$LOGDIR/gameserver.log" 2>&1 &
  fi
fi

for _ in {1..100}; do
  gateway_is_embodied && break
  sleep .05
done
gateway_is_embodied || {
  echo "ERROR embodied GameServer is not ready; see $LOGDIR/gameserver.log" >&2
  exit 2
}

HOST_REUSED=0
DIRECTOR_HOST_REUSED=0
if "$ROOT/gameclient/v1/op/host.sh" --status >/dev/null 2>&1; then
  HOST_REUSED=1
else
  nohup "$ROOT/gameclient/v1/op/host.sh" --start \
    >"$LOGDIR/host.log" 2>&1 &
fi
if "$ROOT/gameclient/v1/op/director-host.sh" --status >/dev/null 2>&1; then
  DIRECTOR_HOST_REUSED=1
else
  nohup "$ROOT/gameclient/v1/op/director-host.sh" --start \
    >"$LOGDIR/director-host.log" 2>&1 &
fi

readiness() {
  PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -m gametable.op.readiness \
      >"$LOGDIR/readiness.json" 2>"$LOGDIR/readiness.err"
}

READY=0
for _ in {1..100}; do
  if readiness; then READY=1; break; fi
  sleep .05
done

# A Host retained from the old Gateway may own a now-invalid transport session.
# Replace only that managed Host; never kill an unrelated process.
if [[ "$READY" -ne 1 && ( "$HOST_REUSED" -eq 1 || "$DIRECTOR_HOST_REUSED" -eq 1 ) ]]; then
  "$ROOT/gameclient/v1/op/host.sh" --restart \
    >"$LOGDIR/host.log" 2>&1 &
  "$ROOT/gameclient/v1/op/director-host.sh" --restart \
    >"$LOGDIR/director-host.log" 2>&1 &
  for _ in {1..100}; do
    if readiness; then READY=1; break; fi
    sleep .05
  done
fi

[[ "$READY" -eq 1 ]] || {
  echo "ERROR embodied stack is not ready; see $LOGDIR" >&2
  if [[ -s "$LOGDIR/readiness.err" ]]; then
    echo "Readiness failure:" >&2
    tail -n 20 "$LOGDIR/readiness.err" >&2 || true
  fi
  for log in gameserver.log host.log director-host.log; do
    if [[ -s "$LOGDIR/$log" ]]; then
      echo "--- $log (tail) ---" >&2
      tail -n 20 "$LOGDIR/$log" >&2 || true
    fi
  done
  exit 2
}
cat "$LOGDIR/readiness.json"

# Prepare the pinned runtime used by both MCP services, without running tests.
"$ROOT/organism/op/organism.sh" prepare >/dev/null

if ! "$ROOT/player-gateway/op/gateway.sh" --status >/dev/null 2>&1; then
  nohup "$ROOT/player-gateway/op/gateway.sh" --start     >"$LOGDIR/player-gateway.log" 2>&1 &
fi

player_gateway_ready() {
  python3 - <<'PY' >/dev/null 2>&1
import json
import urllib.request
with urllib.request.urlopen("http://127.0.0.1:17881/health", timeout=.3) as response:
    value = json.load(response)
raise SystemExit(0 if value.get("component") == "player_gateway" else 1)
PY
}

for _ in {1..100}; do
  player_gateway_ready && break
  sleep .05
done
player_gateway_ready || {
  echo "ERROR Player Gateway is not ready; core services remain running" >&2
  [[ -s "$LOGDIR/player-gateway.log" ]] && tail -n 30 "$LOGDIR/player-gateway.log" >&2 || true
  exit 2
}

echo "WEB UI · Player Gateway: http://127.0.0.1:17881"
op_managed_process start "gametable-vn" "$ROOT" "roleplay.server"   "$ROOT/gametable" "GameTable Юки backend"   env PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"   python3 -m roleplay.server "$@"
