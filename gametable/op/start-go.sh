#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"

# Operator-owned first Director message.
DEFAULT_PROMPT=$(cat <<'EOF'
Привет, Юки. Я Директор, твой руководитель. Сегодня твой первый день стажировки
в нашей лаборатории, и с этого разговора начинается твой трёхчасовой
испытательный срок.

Для начала просто познакомимся. Представься своими словами и расскажи, как ты
себя чувствуешь перед первым рабочим днём.
EOF
)

PROMPT="$DEFAULT_PROMPT"

case "${1:-}" in
  --prompt-file)
    [[ $# -eq 2 ]] || {
      echo "ERROR usage: ./gametable/op/start-go.sh --prompt-file <file>" >&2
      exit 2
    }
    [[ -f "$2" ]] || {
      echo "ERROR prompt file not found: $2" >&2
      exit 2
    }
    PROMPT="$(cat -- "$2")"
    ;;
  "")
    ;;
  *)
    PROMPT="$*"
    ;;
esac

[[ -n "${PROMPT//[[:space:]]/}" ]] || {
  echo "ERROR initial Director prompt must not be empty" >&2
  exit 2
}

# The GUI is part of the normal alpha workstation.  It may start before the
# default Host exists; the GUI client retries login until game_v1 brings the
# Host online.
mkdir -p "$ROOT/gametable/runtime"
GUI_LOG="$ROOT/gametable/runtime/gameclient-gui.log"
if "$ROOT/gameclient/v1/op/gui.sh" --status >/dev/null 2>&1; then
  echo "GameClient GUI v1: already running"
else
  : > "$GUI_LOG"
  "$ROOT/gameclient/v1/op/gui.sh" >"$GUI_LOG" 2>&1 &
  GUI_LAUNCH_PID=$!

  gui_ready=0
  for _ in {1..40}; do
    if "$ROOT/gameclient/v1/op/gui.sh" --status >/dev/null 2>&1; then
      gui_ready=1
      break
    fi
    if ! kill -0 "$GUI_LAUNCH_PID" 2>/dev/null; then
      break
    fi
    sleep 0.05
  done

  if [[ "$gui_ready" -ne 1 ]]; then
    echo "ERROR GameClient GUI v1 did not start" >&2
    cat "$GUI_LOG" >&2 || true
    exit 2
  fi
fi

# OpenCode officially auto-submits TUI --prompt.
exec "$ROOT/gametable/op/start.sh" --fresh --prompt "$PROMPT"
