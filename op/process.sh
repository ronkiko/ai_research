#!/usr/bin/env bash
# Shared foreground process management for long-lived operator launchers.
#
# Ownership contract:
# - the launcher owns only PIDs that it wrote to its own pidfile;
# - status/stop never discover processes by scanning the OS;
# - fixed TCP endpoints are checked separately by the stack launcher.
#
# Keep the historical MARKER/CWD arguments in the public function signatures so
# component launchers remain compatible; MARKER is no longer used for discovery.

op_runtime_dir() {
  if [[ -n "${XDG_RUNTIME_DIR:-}" && -d "${XDG_RUNTIME_DIR}" && -w "${XDG_RUNTIME_DIR}" ]]; then
    printf '%s\n' "${XDG_RUNTIME_DIR}"
  else
    printf '%s\n' "/tmp"
  fi
}

op_tcp_port_in_use() {
  local host="$1"
  local port="$2"
  "${PYTHON:-python3}" - "$host" "$port" <<'PY' >/dev/null 2>&1
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
try:
    with socket.create_connection((host, port), timeout=0.2):
        pass
except OSError:
    raise SystemExit(1)
raise SystemExit(0)
PY
}

op_require_tcp_port_free() {
  local host="$1"
  local port="$2"
  local label="$3"
  if op_tcp_port_in_use "$host" "$port"; then
    echo "ERROR $label cannot start: $host:$port is already occupied" >&2
    echo "Free that port and run the command again." >&2
    return 2
  fi
}

op_root_key() {
  local root="$1"
  printf '%s' "$root" | cksum | awk '{print $1}'
}

op_pid_file() {
  local tag="$1"
  local root="$2"
  printf '%s/ai-research-%s-%s-%s.pid\n' \
    "$(op_runtime_dir)" "$UID" "$(op_root_key "$root")" "$tag"
}

op_process_alive() {
  local pid="$1"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

op_locate_process() {
  local tag="$1"
  local root="$2"
  local _marker="$3"
  local _expected_cwd="$4"
  local pidfile pid

  pidfile="$(op_pid_file "$tag" "$root")"
  [[ -f "$pidfile" ]] || return 1

  read -r pid < "$pidfile" || true
  if [[ -n "${pid:-}" ]] && op_process_alive "$pid"; then
    printf '%s\n' "$pid"
    return 0
  fi

  rm -f "$pidfile"
  return 1
}

op_stop_process() {
  local tag="$1"
  local root="$2"
  local marker="$3"
  local expected_cwd="$4"
  local label="$5"
  local pid pidfile i

  pidfile="$(op_pid_file "$tag" "$root")"
  if ! pid="$(op_locate_process "$tag" "$root" "$marker" "$expected_cwd")"; then
    echo "$label: stopped"
    return 0
  fi

  echo "$label: stopping pid=$pid"
  kill -TERM "$pid" 2>/dev/null || true

  for ((i=0; i<100; i++)); do
    if ! op_process_alive "$pid"; then
      rm -f "$pidfile"
      echo "$label: stopped"
      return 0
    fi
    sleep 0.05
  done

  echo "$label: TERM timeout; killing pid=$pid" >&2
  kill -KILL "$pid" 2>/dev/null || true
  for ((i=0; i<20; i++)); do
    if ! op_process_alive "$pid"; then
      rm -f "$pidfile"
      echo "$label: stopped"
      return 0
    fi
    sleep 0.05
  done

  echo "ERROR $label pid=$pid did not stop" >&2
  return 1
}

op_start_process() {
  local tag="$1"
  local root="$2"
  local marker="$3"
  local expected_cwd="$4"
  local label="$5"
  shift 5
  local pid pidfile

  if pid="$(op_locate_process "$tag" "$root" "$marker" "$expected_cwd")"; then
    echo "ERROR $label is already running pid=$pid" >&2
    echo "Use --restart to replace it." >&2
    return 2
  fi

  pidfile="$(op_pid_file "$tag" "$root")"
  printf '%s\n' "$BASHPID" > "$pidfile"

  echo "$label: starting"
  cd "$expected_cwd"
  exec "$@"
}

op_status_process() {
  local tag="$1"
  local root="$2"
  local marker="$3"
  local expected_cwd="$4"
  local label="$5"
  local pid

  if pid="$(op_locate_process "$tag" "$root" "$marker" "$expected_cwd")"; then
    echo "$label: running pid=$pid"
    return 0
  fi
  echo "$label: stopped"
  return 1
}

op_managed_process() {
  local action="$1"
  local tag="$2"
  local root="$3"
  local marker="$4"
  local expected_cwd="$5"
  local label="$6"
  shift 6

  case "$action" in
    start)
      op_start_process "$tag" "$root" "$marker" "$expected_cwd" "$label" "$@"
      ;;
    stop)
      op_stop_process "$tag" "$root" "$marker" "$expected_cwd" "$label"
      ;;
    restart)
      op_stop_process "$tag" "$root" "$marker" "$expected_cwd" "$label"
      op_start_process "$tag" "$root" "$marker" "$expected_cwd" "$label" "$@"
      ;;
    status)
      op_status_process "$tag" "$root" "$marker" "$expected_cwd" "$label"
      ;;
    *)
      echo "ERROR unknown process action: $action" >&2
      return 2
      ;;
  esac
}
