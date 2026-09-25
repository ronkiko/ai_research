"""Opt-in live smoke: Luna calls, temporary save, no laboratory actions."""
import os
from pathlib import Path
import secrets
import signal
import subprocess
import tempfile
import time

from gametable.roleplay.engine import load_rules
from gametable.roleplay.opencode import BackendError, OpenCode
from gametable.roleplay.runtime import Runtime
from gametable.roleplay.server import TABLE, free_port
from gametable.roleplay.store import Store


def main():
    with tempfile.TemporaryDirectory(prefix="gametable-live-") as temp:
        root = Path(temp)
        port, password = free_port(), secrets.token_urlsafe(32)
        env = dict(os.environ, OPENCODE_SERVER_PASSWORD=password, OPENCODE_SERVER_USERNAME="opencode",
                   GAMELAB_BRAIN_STATE_ROOT=str(root / "lab"))
        with (root / "opencode.log").open("wb") as log:
            process = subprocess.Popen(["opencode", "serve", "--pure", "--hostname", "127.0.0.1", "--port", str(port)],
                cwd=TABLE, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            backend = OpenCode(f"http://127.0.0.1:{port}", TABLE, password=password)
            try:
                for _ in range(120):
                    try:
                        if backend.request("GET", "/global/health", timeout=1).get("healthy"):
                            break
                    except BackendError:
                        time.sleep(0.25)
                else:
                    raise RuntimeError("OpenCode failed to start")
                model = backend.select_model()
                assert model == {"providerID": "openai", "modelID": "gpt-5.6-luna"}, model
                print("LIVE model: openai/gpt-5.6-luna", flush=True)
                mcps = backend.request("GET", "/mcp")
                for name in ("game_v1", "gamelab_v1"):
                    assert mcps.get(name, {}).get("status") == "connected", (name, mcps.get(name))
                    print("LIVE MCP connected:", name, flush=True)
                rules = load_rules()
                store = Store(root / "save.sqlite3", rules)
                runtime = Runtime(store, backend, rules)
                event = {"id": "live-smoke-" + secrets.token_hex(6), "activity": "chat",
                         "text": "Привет, Юки. Я Директор. Сегодня мы впервые знакомимся. Как ты себя чувствуешь?"}
                runtime.submit(event)
                stage = None
                while runtime.thread.is_alive():
                    turn = store.get(event["id"])
                    if turn["stage"] != stage:
                        stage = turn["stage"]
                        print("LIVE stage:", stage, flush=True)
                    runtime.thread.join(timeout=5)
                turn = store.get(event["id"])
                assert turn["status"] == "done", turn.get("error")
                result = turn["result"]
                assert not result["reply"]["fallback"], result["draft_attempts"]
                voices = result["assessments"]
                assert voices["heart"]["session_id"] != voices["head"]["session_id"]
                assert store.state()["revision"] == 1
                print("LIVE reply:", result["reply"]["text"], flush=True)
                print("LIVE stats:", store.state()["stats"], flush=True)
                print("PASS live OpenCode: independent voices, checked reply, atomic save; Director save untouched", flush=True)
                store.close()
            finally:
                backend.close_sessions()
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                except ProcessLookupError:
                    pass


if __name__ == "__main__":
    main()
