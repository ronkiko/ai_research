"""Local visual-novel UI with a private OpenCode worker. Python standard library."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .engine import load_rules
from .opencode import BackendError, OpenCode
from .runtime import Runtime, public_turn
from .store import Store
from .view import available_intent_ids, project_view

TABLE = Path(__file__).resolve().parents[1]
DEFAULT_SAVE = TABLE / "runtime/yuki-vn"


class ConsoleLog:
    def __init__(self):
        self.lock = threading.Lock()

    def write(self, message, level="INFO"):
        with self.lock:
            stamp = datetime.now().strftime("%H:%M:%S")
            print(f"[{stamp}] [{level}] {message}", flush=True)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def normalize_turn_body(body, rules, allowed_intents=None):
    """Compatibility ends here: runtime/store only see DirectorIntent."""
    if not isinstance(body, dict):
        raise ValueError("Неверный формат хода")
    fields = set(body)
    if fields == {"id", "text", "intent_id"}:
        intent_id = body["intent_id"]
    elif fields == {"id", "text", "activity"}:
        intent_id = rules.get("ui_activity_compat", {}).get(body["activity"])
        if not intent_id:
            raise ValueError("Неизвестное занятие")
    else:
        raise ValueError("Неверный формат хода")
    if not isinstance(body["id"], str) or not re.fullmatch(r"[a-zA-Z0-9_-]{8,80}", body["id"]):
        raise ValueError("Неверный идентификатор")
    if not isinstance(body["text"], str) or not 1 <= len(body["text"].strip()) <= 4000:
        raise ValueError("Сообщение должно содержать от 1 до 4000 символов")
    if not isinstance(intent_id, str) or intent_id not in rules["intents"]:
        raise ValueError("Неизвестное намерение Директора")
    if allowed_intents is not None and intent_id not in set(allowed_intents):
        raise ValueError("Это действие недоступно в текущей сцене")
    return {"id": body["id"], "text": body["text"].strip(), "intent_id": intent_id}


class Application:
    def __init__(self, store, runtime, backend, rules, prompt=None):
        self.store, self.runtime, self.backend, self.rules = store, runtime, backend, rules
        self.token = secrets.token_urlsafe(32)
        self.prompt = prompt or ""

    def snapshot(self):
        history = [public_turn(t) for t in self.store.history()]
        state = self.store.state()
        running = next((turn for turn in reversed(history) if turn["status"] == "running"), None)
        view = project_view(state, self.rules, busy=running is not None,
                            stage=running["stage"] if running else None)
        return {"view": view, "history": history, "character": self.rules["character"],
                "model": self.backend.model, "token": self.token,
                "initial_prompt": self.prompt}


def handler_for(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, status, value, content_type="application/json; charset=utf-8"):
            raw = json.dumps(value, ensure_ascii=False).encode() if content_type.startswith("application/json") else value
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(raw)

        def local_request(self):
            host = self.headers.get("Host", "")
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            return host in allowed

        def do_GET(self):
            if not self.local_request():
                return self.send(403, {"error": "Local access only"})
            path = urlparse(self.path).path
            if path == "/api/state":
                return self.send(200, app.snapshot())
            if path.startswith("/api/audit/"):
                event_id = path.rsplit("/", 1)[1]
                turn = app.store.get(event_id)
                if not turn or turn["status"] == "running":
                    return self.send(404, {"error": "Аудит ещё недоступен"})
                result = turn["result"]
                return self.send(200, {"status": turn["status"], "error": turn["error"],
                    "assessments": result.get("assessments"), "calculations": result.get("calculations"),
                    "contract": result.get("contract"), "before": result.get("before"), "after": result.get("after"),
                    "laboratory": result.get("laboratory"),
                    "checks": [{"review": a.get("review"), "rejected": a.get("rejected")}
                               for a in result.get("draft_attempts", [])]})
            files = {"/": ("index.html", "text/html; charset=utf-8"),
                     "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                     "/style.css": ("style.css", "text/css; charset=utf-8")}
            if path in files:
                filename, mime = files[path]
                return self.send(200, (TABLE / "web" / filename).read_bytes(), mime)
            self.send(404, {"error": "Not found"})

        def do_POST(self):
            if not self.local_request() or self.headers.get("X-GameTable-Token") != app.token:
                return self.send(403, {"error": "Обнови страницу перед отправкой"})
            if urlparse(self.path).path != "/api/turn":
                return self.send(404, {"error": "Not found"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 20000:
                    raise ValueError("Сообщение слишком большое")
                body = json.loads(self.rfile.read(length))
                allowed = available_intent_ids(app.store.state(), app.rules)
                event = normalize_turn_body(body, app.rules, allowed)
                turn = app.runtime.submit(event)
                self.send(202, public_turn(turn))
            except (ValueError, TypeError) as exc:
                self.send(409 if "текущего" in str(exc) else 400, {"error": str(exc)})
    return Handler


def main():
    parser = argparse.ArgumentParser(description="GameTable — Юки / visual novel")
    parser.add_argument("--port", type=int, default=17880)
    parser.add_argument("--model", help="OpenCode provider/model; по умолчанию из настройки OpenCode")
    parser.add_argument("--variant", help="Вариант модели OpenCode")
    parser.add_argument("--prompt", help="Начальный текст в поле ввода (отправляется Директором)")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be 1..65535")
    logger = ConsoleLog()
    logger.write("GameTable Юки: starting")
    rules = load_rules()
    root = DEFAULT_SAVE
    root.mkdir(parents=True, exist_ok=True)
    # One owner of this save, including launches outside the shell wrapper.
    import fcntl
    lock_file = (root / "owner.lock").open("w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("GameTable уже использует это сохранение")
    store = Store(root / "save.sqlite3", rules)
    port, password = free_port(), secrets.token_urlsafe(32)
    env = dict(os.environ, OPENCODE_SERVER_PASSWORD=password, OPENCODE_SERVER_USERNAME="opencode")
    # No inherited timers/provenance plugins: this process owns all roleplay transitions.
    backend = OpenCode(f"http://127.0.0.1:{port}", TABLE, args.model, args.variant, password,
                       log=logger.write)
    logfile = (root / "opencode.log").open("ab")
    process = subprocess.Popen(["opencode", "serve", "--pure", "--hostname", "127.0.0.1", "--port", str(port)],
        cwd=TABLE, env=env, stdout=logfile, stderr=subprocess.STDOUT, start_new_session=True)
    server = None
    stopped = threading.Event()
    def stop(*_):
        stopped.set()
        # Also interrupts startup/model requests; don't strand the worker when
        # the process-manager's bounded TERM grace period ends.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        if server:
            threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        for _ in range(120):
            if stopped.is_set() or process.poll() is not None:
                raise BackendError("OpenCode остановлен при запуске; см. runtime/yuki-vn/opencode.log")
            try:
                if backend.request("GET", "/global/health", timeout=1).get("healthy"):
                    break
            except BackendError:
                time.sleep(0.25)
        else:
            raise BackendError("OpenCode не запустился за 30 секунд")
        backend.select_model()
        logger.write(f"OpenCode: healthy; model={backend.model}")
        try:
            mcps = backend.request("GET", "/mcp") or {}
            for name in ("game_v1", "gamelab_v1"):
                status = (mcps.get(name) or {}).get("status", "missing")
                level = "INFO" if status == "connected" else "WARN"
                logger.write(f"MCP {name}: {status}", level)
        except BackendError as exc:
            logger.write(f"MCP status unavailable: {exc}", "WARN")
        manuals = "\n\n".join(p.read_text() for p in sorted((TABLE / ".opencode/skills").glob("00[12]*/SKILL.md")))
        runtime = Runtime(store, backend, rules, manuals, log=logger.write)
        app = Application(store, runtime, backend, rules, args.prompt)
        server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(app))
        logger.write(f"GameTable · Юки: http://127.0.0.1:{args.port}")
        logger.write(f"Модель: {backend.model}")
        logger.write("Ctrl+C — сохранить и выйти.")
        if not stopped.is_set():
            server.serve_forever(poll_interval=0.25)
    except (BackendError, OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        if server:
            server.server_close()
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        except ProcessLookupError:
            pass
        logfile.close()
        # OS closes SQLite after worker shutdown. An unfinished turn is marked failed at next boot.
        lock_file.close()


if __name__ == "__main__":
    main()
