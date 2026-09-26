"""Local visual-novel UI with a private OpenCode worker. Python standard library."""
from __future__ import annotations

import argparse
from collections import deque
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
from urllib.parse import parse_qs, urlparse

from .engine import load_rules
from .opencode import BackendError, OpenCode
from .runtime import Runtime, public_turn
from .store import Store, default_story_flow
from .view import available_intent_ids, project_view
from gametable.migration import active_save_root, migration_manifest
from organism.host import HostClient, HostError
from gametable.story import StoryFlow, StoryFlowError

TABLE = Path(__file__).resolve().parents[1]

class ConsoleLog:
    def __init__(self):
        self.lock = threading.Lock()

    def write(self, message, level="INFO"):
        with self.lock:
            stamp = datetime.now().strftime("%H:%M:%S")
            print(f"[{stamp}] [{level}] {message}", flush=True)


class EventHub:
    """Small in-process SSE journal. Events are hints; SQLite remains authoritative."""
    def __init__(self, limit=128):
        self.condition = threading.Condition()
        self.events = deque(maxlen=limit)
        self.next_id = 1

    def publish(self, name, payload):
        if not isinstance(name, str) or not name:
            raise ValueError("SSE event name required")
        with self.condition:
            item = {"id": self.next_id, "event": name, "data": dict(payload)}
            self.next_id += 1
            self.events.append(item)
            self.condition.notify_all()
            return dict(item)

    def since(self, last_id):
        with self.condition:
            return [dict(item) for item in self.events if item["id"] > last_id]

    def wait(self, last_id, timeout=10):
        with self.condition:
            ready = [dict(item) for item in self.events if item["id"] > last_id]
            if ready:
                return ready
            self.condition.wait(timeout)
            return [dict(item) for item in self.events if item["id"] > last_id]


class WorldObservationPump:
    """Persist Yuki's authoritative Host observation independently of browser reads."""

    def __init__(self, store, *, client=None, hz=10.0):
        self.store = store
        self.client = client or HostClient("gametable-world-observer")
        self.period = 1.0 / float(hz)
        self.stop_event = threading.Event()
        self.thread = None
        self.last_signature = None

    @staticmethod
    def public_observation(observation):
        if not isinstance(observation, dict):
            raise ValueError("Host returned no authoritative world observation")
        physical = observation.get("physical") or {}
        return {
            "entity_id": observation.get("entity_id"),
            "world_id": observation.get("world_id"),
            "world_epoch": observation.get("world_epoch"),
            "observed_tick": observation.get("tick"),
            "world_revision": observation.get("world_revision"),
            "location_id": observation.get("zone_id"),
            "physical": {
                "x": physical.get("x"),
                "vx": physical.get("vx"),
                "effort": physical.get("effort"),
            },
        }

    def observe_once(self):
        state = self.client.state()
        observed = self.public_observation(state.get("observation"))
        physical = observed.get("physical") or {}
        x = physical.get("x")
        signature = (
            observed.get("world_epoch"),
            observed.get("location_id"),
            None if not isinstance(x, (int, float)) else int(float(x)),
        )
        if signature != self.last_signature:
            event_key = (
                f"world.live.{observed.get('world_epoch')}:"
                f"{observed.get('world_revision')}:"
                f"{observed.get('observed_tick')}"
            )
            self.store.record_world_event(event_key, "world_observation", observed)
            self.last_signature = signature
        return observed

    def _loop(self):
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                self.observe_once()
            except (HostError, ValueError):
                pass
            delay = max(0.0, self.period - (time.monotonic() - started))
            self.stop_event.wait(delay)

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(
            target=self._loop,
            name="gametable-world-observer",
            daemon=True,
        )
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
        self.client.close()


def sse_frame(item):
    payload = json.dumps(item["data"], ensure_ascii=False, separators=(",", ":"))
    return f'id: {item["id"]}\nevent: {item["event"]}\ndata: {payload}\n\n'.encode()


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def normalize_turn_body(body, rules, allowed_intents=None):
    """Validate the only public DirectorIntent request contract."""
    if not isinstance(body, dict) or set(body) != {"id", "text", "intent_id"}:
        raise ValueError("Неверный формат хода")
    intent_id = body["intent_id"]
    if not isinstance(body["id"], str) or not re.fullmatch(r"[a-zA-Z0-9_-]{8,80}", body["id"]):
        raise ValueError("Неверный идентификатор")
    if not isinstance(body["text"], str) or not 1 <= len(body["text"].strip()) <= 4000:
        raise ValueError("Сообщение должно содержать от 1 до 4000 символов")
    if not isinstance(intent_id, str) or intent_id not in rules["intents"]:
        raise ValueError("Неизвестное намерение Директора")
    if allowed_intents is not None and intent_id not in set(allowed_intents):
        raise ValueError("Это действие недоступно в текущей сцене")
    return {"id": body["id"], "text": body["text"].strip(), "intent_id": intent_id}


class _StaticStory:
    """Test/embedding fallback. Production main always injects durable StoryFlow."""
    def snapshot(self):
        value = default_story_flow()
        value["ui_sessions"] = 0
        value["presentation_mode"] = "vn_dialogue"
        return value

    def presence_open(self, _session_id):
        pass

    def presence_close(self, _session_id):
        pass

    def respond(self, **_kwargs):
        raise StoryFlowError("story flow is not active")

    def director_acquire(self, *_args, **_kwargs):
        raise StoryFlowError("Director control is not active")

    director_input = director_acquire
    director_release = director_acquire


class Application:
    def __init__(
        self, store, runtime, backend, rules, prompt=None, events=None,
        story=None, **_compat,
    ):
        self.store, self.runtime, self.backend, self.rules = store, runtime, backend, rules
        self.token = secrets.token_urlsafe(32)
        self.prompt = prompt or ""
        self.events = events or EventHub()
        self.story = story or _StaticStory()

    def snapshot(self):
        # Observation/reconciliation is independent of browser polling.
        history = [public_turn(t) for t in self.store.history()]
        state = self.store.state()
        running = next((turn for turn in reversed(history) if turn["status"] == "running"), None)
        world_observation = self.store.latest_world_observation()
        story = self.story.snapshot()
        view = project_view(
            state, self.rules, busy=running is not None,
            stage=running["stage"] if running else None,
            frame_ref=None,
            presentation_mode=story["presentation_mode"],
            world_observation=world_observation,
        )
        return {
            "view": view, "history": history, "dialogue": self.store.dialogue(),
            "actions": self.store.active_actions(),
            "character": self.rules["character"],
            "model": self.backend.model, "token": self.token,
            "initial_prompt": self.prompt,
            "story": story,
        }

def handler_for(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def security_headers(self):
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'",
            )

        def send(self, status, value, content_type="application/json; charset=utf-8"):
            raw = (json.dumps(value, ensure_ascii=False).encode()
                   if content_type.startswith("application/json") else value)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.security_headers()
            self.end_headers()
            self.wfile.write(raw)

        def local_request(self):
            host = self.headers.get("Host", "")
            allowed = {f"127.0.0.1:{self.server.server_port}",
                       f"localhost:{self.server.server_port}"}
            return host in allowed

        def stream_hub(self, hub, retry_ms=1500, *, presence_id=None):
            raw_last = self.headers.get("Last-Event-ID", "0")
            try:
                last_id = max(0, int(raw_last))
            except (TypeError, ValueError):
                last_id = 0
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Connection", "keep-alive")
            self.security_headers()
            self.end_headers()
            if presence_id is not None:
                app.story.presence_open(presence_id)
            try:
                self.wfile.write(f"retry: {retry_ms}\n\n".encode())
                self.wfile.flush()
                while True:
                    items = hub.wait(last_id, timeout=10)
                    if not items:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        continue
                    for item in items:
                        self.wfile.write(sse_frame(item))
                        last_id = item["id"]
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
            finally:
                if presence_id is not None:
                    app.story.presence_close(presence_id)

        def stream_events(self, source_id=None):
            identity = (
                "ui." + source_id
                if isinstance(source_id, str) and source_id
                else "ui." + secrets.token_hex(10)
            )
            try:
                return self.stream_hub(app.events, presence_id=identity)
            finally:
                if isinstance(source_id, str) and source_id:
                    app.story.director_disconnect(source_id)

        def do_GET(self):
            if not self.local_request():
                return self.send(403, {"error": "Local access only"})
            path = urlparse(self.path).path
            if path == "/api/state":
                return self.send(200, app.snapshot())
            if path == "/api/events":
                query = parse_qs(urlparse(self.path).query)
                source_id = (query.get("source_id") or [None])[0]
                return self.stream_events(source_id)
            if path.startswith("/api/audit/"):
                event_id = path.rsplit("/", 1)[1]
                turn = app.store.get(event_id)
                if not turn or turn["status"] == "running":
                    return self.send(404, {"error": "Аудит ещё недоступен"})
                result = turn["result"]
                return self.send(200, {"status": turn["status"], "error": turn["error"],
                    "assessments": result.get("assessments"),
                    "calculations": result.get("calculations"),
                    "contract": result.get("contract"), "before": result.get("before"),
                    "after": result.get("after"), "actions": result.get("actions"),
                    "narration_facts": result.get("narration_facts"),
                    "checks": [{"review": a.get("review"), "rejected": a.get("rejected")}
                               for a in result.get("draft_attempts", [])]})
            self.send(404, {"error": "Not found"})

        def read_json_body(self):
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 20000:
                raise ValueError("Сообщение слишком большое")
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise ValueError("JSON body must be an object")
            return value

        def do_POST(self):
            if not self.local_request() or self.headers.get("X-GameTable-Token") != app.token:
                return self.send(403, {"error": "Обнови страницу перед отправкой"})
            path = urlparse(self.path).path
            try:
                body = self.read_json_body()
                if path == "/api/turn":
                    allowed = available_intent_ids(
                        app.store.state(), app.rules, app.store.latest_world_observation()
                    )
                    event = normalize_turn_body(body, app.rules, allowed)
                    turn = app.runtime.submit(event)
                    return self.send(202, public_turn(turn))
                if path == "/api/story/escort-response":
                    result = app.story.respond(
                        offer_id=body.get("offer_id"),
                        response=body.get("response"),
                        text=body.get("text"),
                    )
                    return self.send(200, {"story": result})
                if path == "/api/director/control/acquire":
                    result = app.story.director_acquire(
                        body.get("source_id"),
                        transfer=body.get("transfer") is True,
                    )
                    return self.send(200, result)
                if path == "/api/director/input":
                    move_x = body.get("move_x")
                    if type(move_x) is not int or move_x not in {-1, 0, 1}:
                        raise ValueError("move_x must be -1, 0, or 1")
                    result = app.story.director_input(
                        body.get("source_id"),
                        body.get("lease_id"),
                        move_x,
                    )
                    return self.send(200, result)
                if path == "/api/director/control/release":
                    result = app.story.director_release(
                        body.get("source_id"),
                        body.get("lease_id"),
                    )
                    return self.send(200, result)
                return self.send(404, {"error": "Not found"})
            except (ValueError, TypeError, StoryFlowError) as exc:
                message_text = str(exc)
                conflict = (
                    "Дождись завершения текущего хода" in message_text
                    or "already" in message_text
                    or "уже принадлежит" in message_text
                )
                self.send(409 if conflict else 400, {"error": message_text})
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
    root = active_save_root()
    root.mkdir(parents=True, exist_ok=True)
    import fcntl
    lock_file = (root / "owner.lock").open("w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("GameTable уже использует это сохранение")
    manifest = migration_manifest()
    identity_binding = None if manifest is None else manifest.get("identity_binding")
    if manifest is not None and not isinstance(identity_binding, dict):
        raise SystemExit("active embodied migration has no identity binding")
    try:
        store = Store(
            root / "save.sqlite3",
            rules,
            identity_binding=identity_binding,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    from .external import ActionExecutor
    from .mcp_bridge import MCPBridge
    actions = ActionExecutor(store=store)
    bridge = MCPBridge(actions)
    bridge.start()
    port, password = free_port(), secrets.token_urlsafe(32)
    env = dict(os.environ, OPENCODE_SERVER_PASSWORD=password, OPENCODE_SERVER_USERNAME="opencode")
    env.update(GAMETABLE_MCP_BRIDGE_URL=bridge.url, GAMETABLE_MCP_BRIDGE_SECRET=bridge.secret)
    backend = OpenCode(f"http://127.0.0.1:{port}", TABLE, args.model, args.variant, password,
                       log=logger.write)
    logfile = (root / "opencode.log").open("ab")
    process = subprocess.Popen(
        ["opencode", "serve", "--pure", "--hostname", "127.0.0.1", "--port", str(port)],
        cwd=TABLE, env=env, stdout=logfile, stderr=subprocess.STDOUT, start_new_session=True)
    server = None
    stopped = threading.Event()

    def stop(*_):
        stopped.set()
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
                raise BackendError(f"OpenCode остановлен при запуске; см. {root / 'opencode.log'}")
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
            for name in ("navigation_v1", "learning_v1"):
                status = (mcps.get(name) or {}).get("status", "missing")
                level = "INFO" if status == "connected" else "WARN"
                logger.write(f"MCP {name}: {status}", level)
        except BackendError as exc:
            logger.write(f"MCP status unavailable: {exc}", "WARN")
        manuals = "\n\n".join(
            p.read_text() for p in sorted((TABLE / ".opencode/skills").glob("00[12]*/SKILL.md")))
        events = EventHub()
        actions.backend = backend
        runtime = Runtime(
            store, backend, rules, manuals,
            log=logger.write, events=events.publish, actions=actions,
        )
        story = StoryFlow(store, events=events.publish)
        story.attach_runtime(runtime)
        story.start()
        world_observer = WorldObservationPump(store)
        world_observer.start()
        runtime.start_observer()
        app = Application(
            store, runtime, backend, rules, args.prompt,
            events=events, story=story,
        )
        server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(app))
        logger.write(f"GameTable · Юки backend: http://127.0.0.1:{args.port}")
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
            if "story" in locals():
                story.close()
        except Exception:
            pass
        try:
            if "world_observer" in locals():
                world_observer.close()
        except Exception:
            pass
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        except ProcessLookupError:
            pass
        if 'runtime' in locals():
            runtime.close()
        bridge.close()
        logfile.close()
        lock_file.close()


if __name__ == "__main__":
    main()
