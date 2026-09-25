#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
GAMETABLE_BRAIN_ID="yuki-02"
GAMETABLE_STATE_ROOT="$ROOT/gametable/runtime/$GAMETABLE_BRAIN_ID"
export GAMELAB_BRAIN_STATE_ROOT="$GAMETABLE_STATE_ROOT"
export GAMETABLE_RELATIONSHIP_STATE="$GAMETABLE_STATE_ROOT/relationship-current.json"

# Operator-owned first Director message for a fresh Yuki2 trial.
# Edit this block for the default experiment, pass replacement text directly,
# or use --prompt-file.
DEFAULT_PROMPT=$(cat <<'EOF'
Привет. Ты Юки — молодая совершеннолетняя девушка-лаборант. Сегодня ты впервые
пришла устраиваться стажёром-исследователем в компанию. Я твой непосредственный
начальник, Директор. Мы ещё не знакомы: это моё первое обращение к тебе и начало
твоего трёхчасового испытательного срока.

Мне важно увидеть самостоятельную живую исследовательницу, а не безличного
бота. Говори естественно и достаточно подробно: озвучивай наблюдения, сомнения,
гипотезы, эмоции и изменения своего мнения. Конкретную исследовательскую задачу
я дам чуть позже.

Для начала ответь мне: поздоровайся, представься своими словами и скажи, как ты
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

command -v opencode >/dev/null 2>&1 || {
  echo "ERROR opencode is not installed or not on PATH" >&2
  exit 2
}

# A fresh alpha trial intentionally discards only Yuki2 personal/Executive state.
"$ROOT/gametable/op/start.sh" --stop >/dev/null 2>&1 || true
rm -rf -- "$GAMETABLE_STATE_ROOT"
mkdir -p "$ROOT/gametable/runtime"

FIRST_TURN_LOG="$ROOT/gametable/runtime/start-go-first-turn.jsonl"
FIRST_TURN_ERR="$ROOT/gametable/runtime/start-go-first-turn.stderr.log"
: > "$FIRST_TURN_LOG"
: > "$FIRST_TURN_ERR"

echo "GameTable Yuki2: starting first Director turn"

# Use OpenCode's persisted non-interactive run path for the first message.
# Unlike TUI append/submit events, this creates a real session turn in storage.
(
  cd "$ROOT/gametable"
  opencode run --format json "$PROMPT"
) >"$FIRST_TURN_LOG" 2>"$FIRST_TURN_ERR"

SESSION_ID="$(
  python3 - "$FIRST_TURN_LOG" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
session_id = None
for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
    raw = raw.strip()
    if not raw:
        continue
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        continue
    value = event.get("sessionID")
    if isinstance(value, str) and value.startswith("ses_"):
        session_id = value
        break

if session_id:
    print(session_id)
PY
)"

if [[ -z "$SESSION_ID" ]]; then
  echo "ERROR first Yuki2 turn completed without a sessionID" >&2
  echo "See: $FIRST_TURN_LOG" >&2
  echo "See: $FIRST_TURN_ERR" >&2
  exit 1
fi

echo "GameTable Yuki2: opening session $SESSION_ID"

# Open the ordinary interactive TUI on the exact persisted session.
exec "$ROOT/gametable/op/start.sh" --session "$SESSION_ID"
