#!/usr/bin/env bash
set -Eeuo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

python_bin="${PYTHON:-python3}"
port="${PORT:-8765}"
episodes="${EPISODES:-1000}"
fresh_args=()
case "${FRESH:-0}" in
    0|'')
        training_mode='resume'
        ;;
    1)
        training_mode='fresh'
        fresh_args=(--fresh)
        ;;
    *)
        printf 'FRESH must be 0 or 1\n' >&2
        exit 2
        ;;
esac
window_pid=''
mlp_pid=''

cleanup() {
    local status=$?
    trap - EXIT INT TERM
    if [[ -n "$mlp_pid" ]] && kill -0 "$mlp_pid" 2>/dev/null; then
        kill "$mlp_pid" 2>/dev/null || true
    fi
    if [[ -n "$window_pid" ]] && kill -0 "$window_pid" 2>/dev/null; then
        kill "$window_pid" 2>/dev/null || true
    fi
    wait "$mlp_pid" 2>/dev/null || true
    wait "$window_pid" 2>/dev/null || true
    exit "$status"
}

trap cleanup EXIT INT TERM

printf 'training_mode=%s episodes=%s port=%s\n' "$training_mode" "$episodes" "$port" >&2

"$python_bin" engine.py --mode mlp --window --port "$port" &
window_pid=$!

# The MLP runner retries while the window process finishes starting its socket.
"$python_bin" mlp_runner.py --mode train --episodes "$episodes" --port "$port" \
    "${fresh_args[@]}" &
mlp_pid=$!

# Whichever process ends first determines the session: closing the window stops
# training, and completing/failing training closes the spectator window.
set +e
wait -n "$window_pid" "$mlp_pid"
status=$?
set -e
exit "$status"
