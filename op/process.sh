#!/usr/bin/env bash
# Shared foreground process management for long-lived operator launchers.
# Source this file; call op_managed_process ACTION TAG MARKER CWD COMMAND...

op_runtime_dir() {
  if [[ -n "${XDG_RUNTIME_DIR:-}" && -d "${XDG_RUNTIME_DIR}" && -w "${XDG_RUNTIME_DIR}" ]]; then
    printf '%s\n' "${XDG_RUNTIME_DIR}"
  else
    printf '%s\n' "/tmp"
  fi
}

op_root_key() {
  local root="$1"
  printf '%s' "$root" | cksum | awk '{print $1}'
}

op_pid_file() {
  local tag="$1"
  local root="$2"
  printf '%s/ai-research-%s-%s-%s.pid\n'     "$(op_runtime_dir)" "$UID" "$(op_root_key "$root")" "$tag"
}

op_proc_starttime() {
  local pid="$1"
  local stat rest
  [[ -r "/proc/$pid/stat" ]] || return 1
  stat="$(<"/proc/$pid/stat")"
  rest="${stat#*) }"
  set -- $rest
  [[ $# -ge 20 ]] || return 1
  printf '%s\n' "${20}"
}

op_process_alive() {
  local pid="$1"
  local expected_start="$2"
  local actual_start
  kill -0 "$pid" 2>/dev/null || return 1
  actual_start="$(op_proc_starttime "$pid" 2>/dev/null)" || return 1
  [[ "$actual_start" == "$expected_start" ]]
}

op_find_legacy_process() {
  local marker="$1"
  local expected_cwd="$2"
  local proc pid cmdline cwd
  for proc in /proc/[0-9]*; do
    pid="${proc##*/}"
    [[ "$pid" != "$BASHPID" ]] || continue
    [[ -r "$proc/cmdline" && -e "$proc/cwd" ]] || continue
    cmdline="$(tr '\0' ' ' < "$proc/cmdline" 2>/dev/null || true)"
    [[ "$cmdline" == *"$marker"* ]] || continue
    cwd="$(readlink -f "$proc/cwd" 2>/dev/null || true)"
    [[ "$cwd" == "$expected_cwd" ]] || continue
    printf '%s %s\n' "$pid" "$(op_proc_starttime "$pid")"
    return 0
  done
  return 1
}

op_locate_process() {
  local tag="$1"
  local root="$2"
  local marker="$3"
  local expected_cwd="$4"
  local pidfile pid start legacy

  pidfile="$(op_pid_file "$tag" "$root")"
  if [[ -f "$pidfile" ]]; then
    read -r pid start < "$pidfile" || true
    if [[ -n "${pid:-}" && -n "${start:-}" ]] && op_process_alive "$pid" "$start"; then
      printf '%s %s\n' "$pid" "$start"
      return 0
    fi
    rm -f "$pidfile"
  fi

  if legacy="$(op_find_legacy_process "$marker" "$expected_cwd")"; then
    printf '%s\n' "$legacy" > "$pidfile"
    printf '%s\n' "$legacy"
    return 0
  fi
  return 1
}

op_stop_process() {
  local tag="$1"
  local root="$2"
  local marker="$3"
  local expected_cwd="$4"
  local label="$5"
  local info pid start pidfile i

  pidfile="$(op_pid_file "$tag" "$root")"
  if ! info="$(op_locate_process "$tag" "$root" "$marker" "$expected_cwd")"; then
    echo "$label: stopped"
    return 0
  fi

  read -r pid start <<<"$info"
  echo "$label: stopping pid=$pid"
  kill -TERM "$pid" 2>/dev/null || true

  for ((i=0; i<100; i++)); do
    if ! op_process_alive "$pid" "$start"; then
      rm -f "$pidfile"
      echo "$label: stopped"
      return 0
    fi
    sleep 0.05
  done

  echo "$label: TERM timeout; killing pid=$pid" >&2
  kill -KILL "$pid" 2>/dev/null || true
  for ((i=0; i<20; i++)); do
    if ! op_process_alive "$pid" "$start"; then
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
  local info pid start pidfile

  if info="$(op_locate_process "$tag" "$root" "$marker" "$expected_cwd")"; then
    read -r pid start <<<"$info"
    echo "ERROR $label is already running pid=$pid" >&2
    echo "Use --restart to replace it." >&2
    return 2
  fi

  pidfile="$(op_pid_file "$tag" "$root")"
  start="$(op_proc_starttime "$BASHPID")"
  printf '%s %s\n' "$BASHPID" "$start" > "$pidfile"
  trap 'rm -f "'"$pidfile"'"' EXIT

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
  local info pid start

  if info="$(op_locate_process "$tag" "$root" "$marker" "$expected_cwd")"; then
    read -r pid start <<<"$info"
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
