"""Real joystick-style GameLab inference smoke through shared GameClient Host."""
from __future__ import annotations

import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import time

import torch

from gamelab.host import HostClient, player_from_state
from gamelab.models import SensorHistory, SpineMotorPolicy, motor_state, sensor_frame
from gamelab.runtime import ensure_player


ROOT = Path(__file__).resolve().parents[2]
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


def stop_group(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
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


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="gamelab-smoke-") as temp:
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
            operator: HostClient | None = None
            lab: HostClient | None = None
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

                operator = HostClient("operator-smoke")
                login = operator.login("player1")
                if login.get("reused"):
                    raise AssertionError("fresh Host unexpectedly reused player session")

                operator_session = operator.session()
                if operator_session.get("sequence") != 0:
                    raise AssertionError(f"fresh session sequence must be 0: {operator_session}")

                lab = HostClient("gamelab-smoke")
                state = ensure_player(lab, "player1")
                player = player_from_state(state)
                if float(player["x"]) != 100.0:
                    raise AssertionError(f"P must start at 100, got {player['x']}")

                model = SpineMotorPolicy.fresh(31)
                model.eval()
                history = SensorHistory(
                    sensor_frame(
                        x=player["x"],
                        vx=player["vx"],
                        move_x=player["move_x"],
                        target_x=987.0,
                    )
                )
                with torch.no_grad():
                    goal, _ = model.spine(history.tensor())
                    logits = model.motor(
                        goal,
                        motor_state(vx=player["vx"], move_x=player["move_x"]),
                    )
                    action = int(logits.argmax().item())
                move_x = model.action_to_move(action)

                queued = lab.input(move_x)
                if queued.get("sequence") != 1:
                    raise AssertionError(f"first lab command must share Host sequence 1: {queued}")

                time.sleep(0.1)
                observed = operator.state()
                observed_player = player_from_state(observed)
                if observed["session"].get("sequence") != 1:
                    raise AssertionError(f"operator must observe shared sequence 1: {observed}")

                reset = lab.reset()
                if reset.get("sequence") != 1:
                    raise AssertionError(f"reset must preserve Host sequence 1: {reset}")
                deadline = time.monotonic() + 2.0
                reset_state = None
                while time.monotonic() < deadline:
                    reset_state = operator.state()
                    reset_player = player_from_state(reset_state)
                    if (
                        float(reset_player["x"]) == 100.0
                        and float(reset_player["vx"]) == 0.0
                        and int(reset_player["move_x"]) == 0
                    ):
                        break
                    time.sleep(0.01)
                else:
                    raise AssertionError(f"reset did not restore spawn state: {reset_state}")

                if reset_state["session"].get("session_id") != operator_session.get("session_id"):
                    raise AssertionError("reset replaced shared Host session")
                if reset_state["session"].get("sequence") != 1:
                    raise AssertionError("reset changed shared Host sequence")

                events = operator.events(0, limit=20).get("events", [])
                if not any(
                    event.get("kind") == "input"
                    and event.get("client_id") == "gamelab-smoke"
                    for event in events
                ):
                    raise AssertionError(f"GameLab input not visible in shared Host events: {events}")
                if not any(
                    event.get("kind") == "reset"
                    and event.get("client_id") == "gamelab-smoke"
                    for event in events
                ):
                    raise AssertionError(f"GameLab reset event not visible through Host: {events}")
                if any(
                    event.get("kind") in {"login", "logout"}
                    and str(event.get("client_id", "")).startswith("gamelab")
                    for event in events
                ):
                    raise AssertionError(f"GameLab unexpectedly owned Host session lifecycle: {events}")

                if server.poll() is not None or host.poll() is not None:
                    raise AssertionError("backend died during GameLab inference smoke")

                print(
                    "PASS gamelab shared-Host joystick smoke "
                    f"action={move_x} moved_x={observed_player['x']} "
                    "episode_reset_x=100 session_preserved=yes"
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
                if lab is not None:
                    lab.close()
                if operator is not None:
                    try:
                        operator.logout()
                    except Exception:
                        pass
                    operator.close()
                stop_group(host)
                stop_group(server)


if __name__ == "__main__":
    raise SystemExit(main())
