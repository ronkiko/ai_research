#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"

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

PORT="${GAMETABLE_OPENCODE_PORT:-}"
if [[ -z "$PORT" ]]; then
  PORT="$(python3 - <<'PY'
import socket

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)"
fi

[[ "$PORT" =~ ^[0-9]+$ ]] && (( PORT >= 1024 && PORT <= 65535 )) || {
  echo "ERROR GAMETABLE_OPENCODE_PORT must be an unused TCP port in [1024,65535]" >&2
  exit 2
}

export GAMETABLE_START_GO_PROMPT="$PROMPT"
mkdir -p "$ROOT/gametable/runtime"
INJECT_LOG="$ROOT/gametable/runtime/start-go-injector.log"
: > "$INJECT_LOG"

# OpenCode documents /tui/append-prompt + /tui/submit-prompt as the supported
# programmatic way to drive an already-running TUI.  Waiting for both MCP
# servers avoids the startup race seen with the CLI --prompt option.
(
  python3 - "$PORT" "$$" <<'PY'
from __future__ import annotations

import json
import os
import signal
import sys
import time
import urllib.error
import urllib.request


port = int(sys.argv[1])
tui_pid = int(sys.argv[2])
base = f"http://127.0.0.1:{port}"
prompt = os.environ["GAMETABLE_START_GO_PROMPT"]
expected_mcp = ("game_v1", "gamelab_v1")
deadline = time.monotonic() + 180.0


def alive() -> bool:
    try:
        os.kill(tui_pid, 0)
        return True
    except OSError:
        return False


def request(path: str, *, method: str = "GET", body=None):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        base + path,
        data=data,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=1.0) as response:
        raw = response.read()
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


appended = False
last_mcp = None

while time.monotonic() < deadline and alive():
    try:
        health = request("/global/health")
        if not isinstance(health, dict) or health.get("healthy") is not True:
            time.sleep(0.2)
            continue

        mcp = request("/mcp")
        last_mcp = mcp
        if not isinstance(mcp, dict):
            time.sleep(0.2)
            continue
        if any(
            not isinstance(mcp.get(name), dict)
            or mcp[name].get("status") != "connected"
            for name in expected_mcp
        ):
            time.sleep(0.2)
            continue

        if not appended:
            appended = request(
                "/tui/append-prompt",
                method="POST",
                body={"text": prompt},
            ) is True
            if not appended:
                time.sleep(0.2)
                continue

        if request("/tui/submit-prompt", method="POST") is True:
            print("PASS initial Director prompt submitted after MCP readiness")
            raise SystemExit(0)
    except (
        ConnectionError,
        json.JSONDecodeError,
        TimeoutError,
        urllib.error.URLError,
        urllib.error.HTTPError,
    ):
        pass
    time.sleep(0.2)

print(
    "ERROR initial Director prompt was not submitted; "
    f"last_mcp={last_mcp!r}",
    file=sys.stderr,
)
raise SystemExit(1)
PY
) >>"$INJECT_LOG" 2>&1 &

# No --prompt here: the injector sends the first turn only after the TUI and
# both MCP servers are ready.
exec "$ROOT/gametable/op/start.sh" \
  --fresh \
  --hostname 127.0.0.1 \
  --port "$PORT"
