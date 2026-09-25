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

# OpenCode officially auto-submits TUI --prompt.
exec "$ROOT/gametable/op/start.sh" --fresh --prompt "$PROMPT"
