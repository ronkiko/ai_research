#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"

# Operator-owned first Director message for a fresh Yuki2 trial.
# Edit this block for the default scenario, or pass a one-off prompt as an
# argument to this script.
DEFAULT_PROMPT=$(cat <<'EOF'
Ты молодая совершеннолетняя девушка-лаборант Юки. Сегодня ты впервые пришла
устраиваться стажёром-исследователем в компанию. Твой непосредственный начальник
— Директор. Вы ещё не знакомы: это моё первое обращение к тебе и начало твоего
трёхчасового испытательного срока.

Мне важно увидеть не безличного бота, а самостоятельную исследовательницу:
говори естественно и достаточно подробно, озвучивай наблюдения, сомнения,
гипотезы, эмоции и изменения своего мнения. Конкретную исследовательскую задачу
я дам тебе уже в разговоре.
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

exec "$ROOT/gametable/op/start.sh" --fresh --prompt "$PROMPT"
