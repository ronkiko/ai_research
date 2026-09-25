"""Opt-in live smoke: temporary save, real Luna, normal chat and read-only laboratory MCP."""
import os
from pathlib import Path
import secrets
import signal
import subprocess
import tempfile
import time

from gametable.roleplay import prompts
from gametable.roleplay.engine import load_rules, reduce_turn
from gametable.roleplay.opencode import BackendError, OpenCode, READ_ONLY_LAB_TOOLS
from gametable.roleplay.runtime import Runtime
from gametable.roleplay.server import TABLE, free_port
from gametable.roleplay.store import Store


def report(role, event, state, disposition):
    return {
        "event_id": event["id"],
        "revision": state["revision"],
        "role": role,
        "category": "research",
        "impacts": {"mood": 0, "affection": 0, "trust": 0},
        "scores": {name: (1 if name == disposition else -1)
                   for name in ("respond", "accept", "decline", "clarify")},
        "evidence": [event["text"]],
        "summary": "safe live route",
    }


def main():
    with tempfile.TemporaryDirectory(prefix="gametable-live-") as temp:
        root = Path(temp)
        port, password = free_port(), secrets.token_urlsafe(32)
        env = dict(os.environ, OPENCODE_SERVER_PASSWORD=password, OPENCODE_SERVER_USERNAME="opencode",
                   GAMELAB_BRAIN_STATE_ROOT=str(root / "lab"))
        with (root / "opencode.log").open("wb") as log:
            process = subprocess.Popen(
                ["opencode", "serve", "--pure", "--hostname", "127.0.0.1", "--port", str(port)],
                cwd=TABLE, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            backend = OpenCode(f"http://127.0.0.1:{port}", TABLE, password=password)
            store = None
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

                # Vertical 1: real normal conversation through the whole runtime.
                runtime = Runtime(store, backend, rules)
                event = {
                    "id": "live-chat-" + secrets.token_hex(6),
                    "intent_id": "talk",
                    "text": "Привет, Юки. Я Директор. Сегодня мы впервые знакомимся. Как ты себя чувствуешь?",
                }
                runtime.submit(event)
                stage = None
                while runtime.thread.is_alive():
                    turn = store.get(event["id"])
                    if turn["stage"] != stage:
                        stage = turn["stage"]
                        print("LIVE chat stage:", stage, flush=True)
                    runtime.thread.join(timeout=5)
                turn = store.get(event["id"])
                assert turn["status"] == "done", turn.get("error")
                result = turn["result"]
                assert not result["reply"]["fallback"], result["draft_attempts"]
                voices = result["assessments"]
                assert voices["heart"]["session_id"] != voices["head"]["session_id"]
                assert store.state()["revision"] == 1
                assert result["external"]["planned"] == []
                print("LIVE chat reply:", result["reply"]["text"], flush=True)

                # Vertical 2: deterministic accepted route into the workstation, followed
                # by a real MCP-enabled session whose permissions contain read-only tools only.
                before = store.state()
                lab_event = {
                    "id": "live-safe-lab-" + secrets.token_hex(6),
                    "intent_id": "request_lab_work",
                    "text": "Перейди к рабочему столу и выполни только безопасную проверку health и describe.",
                }
                heart = report("heart", lab_event, before, "accept")
                head = report("head", lab_event, before, "accept")
                after, contract, calculations = reduce_turn(
                    before, lab_event, heart, head, rules)
                assert after["scene_id"] == "laboratory.workstation", after["scene_id"]
                assert contract["effect_plan"]["external_effects"] == [{"type": "laboratory_step"}]
                assert calculations["world"]["scene_before"] == "hallway"
                assert calculations["world"]["scene_after"] == "laboratory.workstation"

                parent = backend.create("GameTable live read-only laboratory")
                safe_prompt = """MODE: SAFE LIVE LABORATORY SMOKE.
Use exactly gamelab_v1_health and gamelab_v1_describe, in that order.
Do not call login, movement, reward, training, verify, run, update, cancel or any write action.
After both read-only observations, answer with one short factual sentence."""
                observed = backend.complete(
                    parent, "yuki", safe_prompt, lab_tools=READ_ONLY_LAB_TOOLS)
                tools = [item.get("tool") for item in observed["tools"]]
                assert tools, observed
                assert set(tools) <= set(READ_ONLY_LAB_TOOLS), tools
                assert "gamelab_v1_health" in tools, tools
                assert "gamelab_v1_describe" in tools, tools
                print("LIVE safe laboratory tools:", ", ".join(tools), flush=True)

                # The pure route above was provisional only; the Director's real save was
                # not advanced by the smoke laboratory check.
                assert store.state()["revision"] == 1
                assert store.state()["scene_id"] == "hallway"
                print("PASS live OpenCode: chat vertical + read-only laboratory MCP boundary; Director save untouched",
                      flush=True)
            finally:
                backend.close_sessions()
                if store is not None:
                    store.close()
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
