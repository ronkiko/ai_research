"""End-to-end runtime smoke for the real GameServer -> Host -> CLI vertical."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import time


ROOT = Path(__file__).resolve().parents[3]
SERVER_PORT = 17600
HOST_PORT = 17700


def wait_port(port: int, process: subprocess.Popen, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"process exited before port {port} was ready")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"timeout waiting for 127.0.0.1:{port}")


def stop_group(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=2)


def run_cli(*args: str) -> dict:
    command = [
        str(ROOT / "gameclient/v1/op/cli.sh"),
        "--json",
        *args,
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
        timeout=5,
    )
    line = completed.stdout.strip()
    if not line:
        raise RuntimeError(f"CLI returned no JSON for: {' '.join(args)}")
    return json.loads(line)


def entity(snapshot: dict, entity_id: str) -> dict:
    return next(
        item
        for item in snapshot["entities"]
        if item["entity_id"] == entity_id
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="game-v1-smoke-") as temp:
        temp_path = Path(temp)
        server_log_path = temp_path / "server.log"
        host_log_path = temp_path / "host.log"

        with server_log_path.open("w+", encoding="utf-8") as server_log, host_log_path.open(
            "w+", encoding="utf-8"
        ) as host_log:
            server = subprocess.Popen(
                [str(ROOT / "gameserver/v1/op/server.sh")],
                cwd=ROOT,
                stdout=server_log,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
            host: subprocess.Popen | None = None
            try:
                wait_port(SERVER_PORT, server)

                host = subprocess.Popen(
                    [str(ROOT / "gameclient/v1/op/host.sh")],
                    cwd=ROOT,
                    stdout=host_log,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
                wait_port(HOST_PORT, host)

                players = run_cli("players")
                if "player1" not in players.get("players", []):
                    raise AssertionError(f"player1 missing from lobby: {players}")

                login = run_cli("login", "player1")
                session = login.get("session") or {}
                if session.get("player_id") != "player1":
                    raise AssertionError(f"unexpected login response: {login}")

                before = run_cli("state")
                before_player = entity(before["snapshot"], "actor-player1")
                before_bomb = entity(before["snapshot"], "mob1")
                if before_player["x"] != 100.0:
                    raise AssertionError(f"P must start at x=100, got {before_player['x']}")
                if not 0.0 <= float(before_bomb["x"]) <= 1000.0:
                    raise AssertionError(f"B outside world: {before_bomb['x']}")

                first = run_cli("move", "right")
                if first.get("sequence") != 1:
                    raise AssertionError(f"first Host sequence must be 1: {first}")

                time.sleep(0.25)
                moving = run_cli("state")
                moving_player = entity(moving["snapshot"], "actor-player1")
                if not moving_player["x"] > before_player["x"]:
                    raise AssertionError(
                        f"P did not move right: {before_player['x']} -> {moving_player['x']}"
                    )

                stopped = run_cli("move", "stop")
                if stopped.get("sequence") != 2:
                    raise AssertionError(f"stop sequence must be 2: {stopped}")

                # Zero motor effort relaxes the actuator; velocity decays through
                # physical drag instead of snapping to zero.
                time.sleep(0.05)
                coasting = run_cli("state")
                coasting_player = entity(coasting["snapshot"], "actor-player1")
                if not float(coasting_player["vx"]) > 0.0:
                    raise AssertionError(f"P should still coast after motor release: {coasting_player}")

                time.sleep(2.2)
                stopped_state = run_cli("state")
                stopped_player = entity(stopped_state["snapshot"], "actor-player1")
                if float(stopped_player["vx"]) != 0.0:
                    raise AssertionError(f"P did not settle to rest: {stopped_player}")
                stopped_x = float(stopped_player["x"])
                time.sleep(0.1)
                again = run_cli("state")
                again_x = float(entity(again["snapshot"], "actor-player1")["x"])
                if abs(again_x - stopped_x) > 1e-9:
                    raise AssertionError(f"P moved after physical rest: {stopped_x} -> {again_x}")

                events = run_cli("events")
                input_events = [
                    item
                    for item in events.get("events", [])
                    if item.get("kind") == "input"
                ]
                observed = [
                    (item.get("client_id"), item.get("sequence"), item.get("move_x"))
                    for item in input_events
                ]
                if observed[-2:] != [("cli", 1, 1), ("cli", 2, 0)]:
                    raise AssertionError(f"unexpected shared Host events: {observed}")

                run_cli("logout")

                if server.poll() is not None:
                    raise AssertionError("GameServer died during runtime smoke")
                if host.poll() is not None:
                    raise AssertionError("GameClient Host died during runtime smoke")

                print(
                    "PASS game v1 runtime smoke "
                    f"P:{before_player['x']}->{moving_player['x']} "
                    f"B:{before_bomb['x']}"
                )
                return 0
            except Exception:
                server_log.flush()
                host_log.flush()
                print("----- GameServer log -----")
                print(server_log_path.read_text(encoding="utf-8", errors="replace"))
                print("----- GameClient Host log -----")
                print(host_log_path.read_text(encoding="utf-8", errors="replace"))
                raise
            finally:
                if host is not None:
                    stop_group(host)
                stop_group(server)


if __name__ == "__main__":
    raise SystemExit(main())
