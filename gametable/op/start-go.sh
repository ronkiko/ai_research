#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
PROMPT="Привет, Юки. Я Директор. Сегодня твой первый день в нашей лаборатории. Давай познакомимся. Как ты себя чувствуешь?"
case "${1:-}" in
  --prompt-file)
    [[ $# -eq 2 && -f "$2" ]] || { echo "ERROR usage: start-go.sh --prompt-file <file>" >&2; exit 2; }
    PROMPT="$(cat -- "$2")" ;;
  "") ;;
  *) PROMPT="$*" ;;
esac
[[ -n "${PROMPT//[[:space:]]/}" ]] || { echo "ERROR empty prompt" >&2; exit 2; }
# Prefill only: the Director sends the first turn from the VN screen.
exec "$ROOT/gametable/op/start.sh" --fresh --prompt "$PROMPT"
