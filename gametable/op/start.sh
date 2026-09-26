#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
ACTION="start"
ACTION_EXPLICIT=0
FRESH=0
FREE_PORTS=0
BACKEND_ARGS=()

set_action() {
  local requested="$1"
  if [[ "$ACTION_EXPLICIT" -eq 1 && "$ACTION" != "$requested" ]]; then
    echo "ERROR conflicting lifecycle actions: --$ACTION and --$requested" >&2
    exit 2
  fi
  ACTION="$requested"
  ACTION_EXPLICIT=1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --start) set_action start ;;
    --restart) set_action restart ;;
    --stop) set_action stop ;;
    --status) set_action status ;;
    --fresh) FRESH=1 ;;
    --free-ports) FREE_PORTS=1 ;;
    *) BACKEND_ARGS+=("$1") ;;
  esac
  shift
done
set -- "${BACKEND_ARGS[@]}"

if [[ "$ACTION" == "stop" || "$ACTION" == "status" ]]; then
  [[ "$FRESH" -eq 0 ]] || {
    echo "ERROR --fresh cannot be combined with --$ACTION" >&2
    exit 2
  }
  [[ "$FREE_PORTS" -eq 0 ]] || {
    echo "ERROR --free-ports cannot be combined with --$ACTION" >&2
    exit 2
  }
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
PLAYER_GATEWAY_PORT_VALUE="${PLAYER_GATEWAY_PORT:-17881}"
export PLAYER_GATEWAY_GAMETABLE_PORT="${PLAYER_GATEWAY_GAMETABLE_PORT:-$GAMETABLE_PORT}"

source "$ROOT/op/process.sh"

stop_stack() {
  "$ROOT/player-gateway/op/gateway.sh" --stop
  op_managed_process stop "gametable-vn" "$ROOT" \
    "$ROOT/gametable" "GameTable Юки"
  "$ROOT/gameclient/v1/op/director-host.sh" --stop
  "$ROOT/gameclient/v1/op/host.sh" --stop
  "$ROOT/gameserver/v1/op/embodied.sh" --stop
}

require_runtime_port_free() {
  local port="$1"
  local label="$2"

  if ! op_tcp_port_in_use "127.0.0.1" "$port"; then
    return 0
  fi

  if [[ "$FREE_PORTS" -ne 1 ]]; then
    echo "ERROR $label cannot start: 127.0.0.1:$port is already occupied" >&2
    echo "Free that port manually, or rerun with:" >&2
    echo "  ./gametable/op/start.sh --restart --free-ports" >&2
    return 2
  fi

  command -v fuser >/dev/null || {
    echo "ERROR --free-ports requires the 'fuser' command" >&2
    return 2
  }

  echo "FREE PORT $label · 127.0.0.1:$port"
  fuser -k "$port/tcp" >/dev/null 2>&1 || true

  for _ in {1..40}; do
    if ! op_tcp_port_in_use "127.0.0.1" "$port"; then
      echo "FREE PORT $label · released"
      return 0
    fi
    sleep .05
  done

  echo "ERROR $label port 127.0.0.1:$port is still occupied after --free-ports" >&2
  echo "Free that port manually and run the command again." >&2
  return 2
}

require_stack_ports_free() {
  require_runtime_port_free 17600 "GameServer Gateway"
  require_runtime_port_free 17606 "GameServer embodied World"
  require_runtime_port_free 17700 "Host[yuki]"
  require_runtime_port_free 17701 "Host[director]"
  require_runtime_port_free "$GAMETABLE_PORT" "GameTable backend"
  require_runtime_port_free "$PLAYER_GATEWAY_PORT_VALUE" "Player Gateway"
}

status_stack() {
  "$ROOT/player-gateway/op/gateway.sh" --status || true
  op_managed_process status "gametable-vn" "$ROOT" \
    "$ROOT/gametable" "GameTable Юки" || true
  "$ROOT/gameclient/v1/op/host.sh" --status || true
  "$ROOT/gameclient/v1/op/director-host.sh" --status || true
  "$ROOT/gameserver/v1/op/embodied.sh" --status || true
}

if [[ "$ACTION" == "stop" ]]; then
  [[ $# -eq 0 ]] || { echo "ERROR --stop does not accept backend arguments" >&2; exit 2; }
  stop_stack
  exit 0
fi
if [[ "$ACTION" == "status" ]]; then
  [[ $# -eq 0 ]] || { echo "ERROR --status does not accept backend arguments" >&2; exit 2; }
  status_stack
  exit 0
fi

# Validate prerequisites before a destructive restart/fresh stop.
command -v opencode >/dev/null || { echo "ERROR opencode is not installed" >&2; exit 2; }
command -v python3 >/dev/null || { echo "ERROR python3 is not installed" >&2; exit 2; }
command -v node >/dev/null || { echo "ERROR node is not installed" >&2; exit 2; }
command -v npm >/dev/null || { echo "ERROR npm is not installed" >&2; exit 2; }

# Restart and fresh are orthogonal flags but share one lifecycle boundary:
# stop once, resolve endpoint conflicts once, then start once.
if [[ "$ACTION" == "restart" || "$FRESH" -eq 1 ]]; then
  stop_stack
  require_stack_ports_free
fi

cd "$ROOT"

if op_managed_process status "gametable-vn" "$ROOT" \
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
  # Managed runtime was already stopped and all fixed endpoints were resolved
  # before migration/reset, so destructive state reset happens exactly once.
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

gateway_is_embodied() {
  python3 - <<'PY' >/dev/null 2>&1
from gameserver.v1.common.config import GATEWAY_PORT, HOST
from gameserver.v1.common.protocol import message, rpc
value = rpc(HOST, GATEWAY_PORT, message("health"), timeout=.2)
raise SystemExit(0 if value.get("mode") == "embodied_world_v1" else 1)
PY
}

if ! "$ROOT/gameserver/v1/op/embodied.sh" --status >/dev/null 2>&1; then
  if "$ROOT/gameserver/v1/op/server.sh" --status >/dev/null 2>&1; then
    echo "CUTOVER stop legacy GameServer"
    "$ROOT/gameclient/v1/op/host.sh" --stop
    "$ROOT/gameserver/v1/op/server.sh" --stop
  fi
  require_runtime_port_free 17600 "GameServer Gateway"
  require_runtime_port_free 17606 "GameServer embodied World"
  nohup "$ROOT/gameserver/v1/op/embodied.sh" --start \
    >"$LOGDIR/gameserver.log" 2>&1 &
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
  require_runtime_port_free 17700 "Host[yuki]"
  nohup "$ROOT/gameclient/v1/op/host.sh" --start \
    >"$LOGDIR/host.log" 2>&1 &
fi
if "$ROOT/gameclient/v1/op/director-host.sh" --status >/dev/null 2>&1; then
  DIRECTOR_HOST_REUSED=1
else
  require_runtime_port_free 17701 "Host[director]"
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
  require_runtime_port_free "$PLAYER_GATEWAY_PORT_VALUE" "Player Gateway"
  nohup "$ROOT/player-gateway/op/gateway.sh" --start \
    >"$LOGDIR/player-gateway.log" 2>&1 &
fi

player_gateway_ready() {
  python3 - "$PLAYER_GATEWAY_PORT_VALUE" <<'PY' >/dev/null 2>&1
import json
import sys
import urllib.request
port = int(sys.argv[1])
with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=.3) as response:
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

echo "WEB UI · Player Gateway: http://127.0.0.1:$PLAYER_GATEWAY_PORT_VALUE"
require_runtime_port_free "$GAMETABLE_PORT" "GameTable backend"
op_managed_process start "gametable-vn" "$ROOT" \
  "$ROOT/gametable" "GameTable Юки backend" \
  env PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" \
  python3 -m roleplay.server "$@"
