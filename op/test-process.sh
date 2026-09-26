#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

bash -n \
  op/process.sh \
  gameserver/v1/op/server.sh \
  gameserver/v1/op/embodied.sh \
  gameclient/v1/op/host.sh \
  gameclient/v1/op/director-host.sh \
  gameclient/v1/op/gui.sh \
  gameclient/v1/op/director-gui.sh \
  gametable/op/args.sh \
  gametable/op/start.sh \
  player-gateway/op/gateway.sh

for script in \
  gameserver/v1/op/server.sh \
  gameserver/v1/op/embodied.sh \
  gameclient/v1/op/host.sh \
  gameclient/v1/op/director-host.sh \
  gameclient/v1/op/gui.sh \
  gameclient/v1/op/director-gui.sh \
  player-gateway/op/gateway.sh
do
  grep -q -- '--start' "$script"
  grep -q -- '--stop' "$script"
  grep -q -- '--restart' "$script"
  grep -q -- '--status' "$script"
done
for flag in --start --stop --restart --status --fresh --free-ports; do
  grep -q -- "$flag" gametable/op/args.sh
done

# Managed lifecycle is pidfile-only. Port ownership is checked separately by
# the launcher; process management must never scan /proc or recover by cmdline.
if grep -q -- '/proc/' op/process.sh; then
  echo "ERROR op/process.sh must not inspect /proc" >&2
  exit 1
fi
if grep -q -- 'op_find_legacy_process' op/process.sh; then
  echo "ERROR legacy process discovery returned" >&2
  exit 1
fi
grep -q -- '--port 17700' gameclient/v1/op/host.sh
grep -q -- '--port 17701' gameclient/v1/op/director-host.sh
grep -q -- 'player-gateway/src/server.mjs' player-gateway/op/gateway.sh
grep -q -- '--free-ports' gametable/op/start.sh
grep -q -- 'fuser -k "$port/tcp"' gametable/op/start.sh
grep -q -- './gametable/op/start.sh --restart --free-ports' gametable/op/start.sh

# GameTable operator flags are orthogonal and order-independent; none may leak
# into roleplay.server argv.
# shellcheck source=/dev/null
source "$ROOT/gametable/op/args.sh"

gametable_parse_cli --fresh --restart --free-ports --model test/model --port 19000
[[ "$GAMETABLE_ACTION" == "restart" ]]
[[ "$GAMETABLE_FRESH" -eq 1 ]]
[[ "$GAMETABLE_FREE_PORTS" -eq 1 ]]
[[ "${GAMETABLE_BACKEND_ARGS[*]}" == "--model test/model --port 19000" ]]

gametable_parse_cli --restart --fresh --prompt "hello"
[[ "$GAMETABLE_ACTION" == "restart" ]]
[[ "$GAMETABLE_FRESH" -eq 1 ]]
[[ "$GAMETABLE_FREE_PORTS" -eq 0 ]]
[[ "${GAMETABLE_BACKEND_ARGS[*]}" == "--prompt hello" ]]

gametable_parse_cli --free-ports --fresh --restart
[[ "$GAMETABLE_ACTION" == "restart" ]]
[[ "$GAMETABLE_FRESH" -eq 1 ]]
[[ "$GAMETABLE_FREE_PORTS" -eq 1 ]]
[[ "${#GAMETABLE_BACKEND_ARGS[@]}" -eq 0 ]]

if gametable_parse_cli --start --restart >/dev/null 2>&1; then
  echo "ERROR conflicting lifecycle actions were accepted" >&2
  exit 1
fi
if gametable_parse_cli --status --free-ports >/dev/null 2>&1; then
  echo "ERROR --status --free-ports was accepted" >&2
  exit 1
fi

# shellcheck source=/dev/null
source "$ROOT/op/process.sh"

TAG="process-smoke-${BASHPID}"
LABEL="Managed process smoke"
STARTER=""
PORT_SERVER=""
PORT_FILE=""

cleanup() {
  op_stop_process "$TAG" "$ROOT" "$LABEL" >/dev/null 2>&1 || true
  if [[ -n "$STARTER" ]]; then
    wait "$STARTER" 2>/dev/null || true
  fi
  if [[ -n "$PORT_SERVER" ]]; then
    kill "$PORT_SERVER" 2>/dev/null || true
    wait "$PORT_SERVER" 2>/dev/null || true
  fi
  [[ -z "$PORT_FILE" ]] || rm -f "$PORT_FILE"
}
trap cleanup EXIT

(
  # shellcheck source=/dev/null
  source "$ROOT/op/process.sh"
  op_managed_process     start     "$TAG"     "$ROOT"     "$ROOT"     "$LABEL"     bash -c 'exec -a ai-research-process-smoke sleep 30'
) &
STARTER=$!

for ((i=0; i<100; i++)); do
  if op_status_process "$TAG" "$ROOT" "$LABEL" >/dev/null 2>&1; then
    break
  fi
  sleep 0.02
done

op_status_process "$TAG" "$ROOT" "$LABEL" >/dev/null

if (
  source "$ROOT/op/process.sh"
  op_managed_process     start     "$TAG"     "$ROOT"     "$ROOT"     "$LABEL"     bash -c 'exec -a ai-research-process-smoke sleep 30'
) >/dev/null 2>&1; then
  echo "ERROR duplicate managed start unexpectedly succeeded" >&2
  exit 1
fi

op_stop_process "$TAG" "$ROOT" "$LABEL" >/dev/null
wait "$STARTER" 2>/dev/null || true
STARTER=""

if op_status_process "$TAG" "$ROOT" "$LABEL" >/dev/null 2>&1; then
  echo "ERROR managed process still reported running after stop" >&2
  exit 1
fi

PORT_FILE="$(mktemp)"
python3 - "$PORT_FILE" <<'PY' &
import socket
import sys
import time
from pathlib import Path

sock = socket.socket()
sock.bind(("127.0.0.1", 0))
sock.listen()
Path(sys.argv[1]).write_text(str(sock.getsockname()[1]))
try:
    time.sleep(30)
finally:
    sock.close()
PY
PORT_SERVER=$!

for ((i=0; i<100; i++)); do
  [[ -s "$PORT_FILE" ]] && break
  sleep 0.02
done
[[ -s "$PORT_FILE" ]] || {
  echo "ERROR port preflight smoke did not start listener" >&2
  exit 1
}
TEST_PORT="$(cat "$PORT_FILE")"

if op_require_tcp_port_free "127.0.0.1" "$TEST_PORT" "Port smoke" >/dev/null 2>&1; then
  echo "ERROR occupied TCP endpoint passed strict preflight" >&2
  exit 1
fi

kill "$PORT_SERVER"
wait "$PORT_SERVER" 2>/dev/null || true
PORT_SERVER=""
op_require_tcp_port_free "127.0.0.1" "$TEST_PORT" "Port smoke" >/dev/null

echo "PASS managed operator process smoke"
