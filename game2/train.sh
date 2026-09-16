#!/usr/bin/env bash
set -Eeuo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

if [[ "$#" -gt 1 || ( "$#" -eq 1 && "$1" != auto ) ]]; then
    printf 'usage: ./train.sh [auto]\n' >&2
    exit 2
fi

auto=0
if [[ "$#" -eq 1 ]]; then
    auto=1
fi

python_bin="${PYTHON:-python3}"
port="${PORT:-8765}"
episodes="${EPISODES:-1000}"
speed="${AUTO_SPEED:-100}"
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

if [[ "$auto" -eq 1 ]]; then
    printf 'training_mode=%s episodes=%s port=%s clock_mode=auto speed=%sx renderer=headless\n' \
        "$training_mode" "$episodes" "$port" "$speed" >&2
    "$python_bin" engine.py --mode mlp --auto --speed "$speed" --port "$port" &
else
    printf 'training_mode=%s episodes=%s port=%s clock_mode=realtime renderer=window\n' \
        "$training_mode" "$episodes" "$port" >&2
    "$python_bin" engine.py --mode mlp --window --port "$port" &
fi
window_pid=$!

# The MLP runner retries while the window process finishes starting its socket.
"$python_bin" mlp_runner.py --mode train --episodes "$episodes" --port "$port" \
    "${fresh_args[@]}" &
mlp_pid=$!

# Whichever process ends first determines the session: the game or training
# process ending stops the other process in both realtime and auto modes.
set +e
wait -n "$window_pid" "$mlp_pid"
status=$?
set -e
exit "$status"
