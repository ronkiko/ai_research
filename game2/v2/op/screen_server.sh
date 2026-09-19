#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
RUNTIME="$ROOT/game2/v2/runtime"
PIDFILE="$RUNTIME/screen-server.pid"
LOGFILE="$RUNTIME/screen-server.log"
DISCOVERY="$RUNTIME/screen-server.json"
PYTHON_BIN="${PYTHON:-python3}"

mkdir -p "$RUNTIME"

command="${1:-start}"
if [[ "$command" == "start" || "$command" == "stop" || "$command" == "status" || "$command" == "restart" ]]; then
  shift || true
else
  command="start"
fi

running_pid() {
  if [[ ! -f "$PIDFILE" ]]; then
    return 1
  fi
  local pid
  pid="$(cat "$PIDFILE" 2>/dev/null || true)"
  [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null
}

stop_server() {
  if running_pid; then
    local pid
    pid="$(cat "$PIDFILE")"
    kill "$pid"
    for _ in $(seq 1 50); do
      if ! kill -0 "$pid" 2>/dev/null; then
        break
      fi
      sleep 0.1
    done
    if kill -0 "$pid" 2>/dev/null; then
      kill -KILL "$pid" 2>/dev/null || true
    fi
  fi
  rm -f "$PIDFILE" "$DISCOVERY"
}

case "$command" in
  status)
    if running_pid; then
      echo "Screen Server: running (pid $(cat "$PIDFILE"))"
      exit 0
    fi
    echo "Screen Server: stopped"
    exit 1
    ;;
  stop)
    stop_server
    echo "Screen Server: stopped"
    exit 0
    ;;
  restart)
    stop_server
    ;;
  start)
    ;;
esac

if running_pid; then
  echo "Screen Server: already running (pid $(cat "$PIDFILE"))"
  exit 0
fi

rm -f "$PIDFILE" "$DISCOVERY"
cd "$ROOT"
nohup "$PYTHON_BIN" -m game2.v2.management.screen_server \
  --discovery "$DISCOVERY" \
  --slots "${GAME2_SCREEN_SLOTS:-4}" \
  "$@" >>"$LOGFILE" 2>&1 &
pid=$!
printf '%s\n' "$pid" > "$PIDFILE"

for _ in $(seq 1 50); do
  if [[ -f "$DISCOVERY" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "Screen Server: running (pid $pid)"
    echo "Discovery: $DISCOVERY"
    exit 0
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$PIDFILE"
    echo "Screen Server failed to start; see $LOGFILE" >&2
    exit 1
  fi
  sleep 0.1
done

kill "$pid" 2>/dev/null || true
rm -f "$PIDFILE" "$DISCOVERY"
echo "Screen Server did not become ready; see $LOGFILE" >&2
exit 1
