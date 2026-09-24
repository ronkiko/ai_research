"""Real joystick-style GameLab inference smoke through shared GameClient Host."""
from __future__ import annotations

import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time

import torch

from gamelab.host import HostClient, player_from_state
from gamelab.models import SensorHistory, SpineMotorPolicy, motor_state, sensor_frame
from gamelab.runtime import ensure_player, reset_player_state


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


def main(*, learned_checkpoint: Path | None = None) -> int:
    import gamelab.hosts as catalog_module
    existing = os.environ.get("GAMELAB_TEST_EXISTING_SERVER") == "1"
    saved_port = catalog_module.HOST_PORT
    host_port = HOST_PORT
    player_id = "player1"
    with tempfile.TemporaryDirectory(prefix="gamelab-smoke-") as temp:
        temp_path = Path(temp)
        server_log_path = temp_path / "server.log"
        host_log_path = temp_path / "host.log"
        with server_log_path.open("w+", encoding="utf-8") as server_log, host_log_path.open(
            "w+", encoding="utf-8"
        ) as host_log:
            server = None if existing else subprocess.Popen(
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
                if server is not None:
                    wait_port(SERVER_PORT, server)
                host = None if existing else subprocess.Popen(
                    [sys.executable, "-m", "gameclient.v1.host.server", "--port", str(host_port)],
                    cwd=ROOT,
                    stdout=host_log,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
                if host is not None:
                    wait_port(host_port, host)

                operator = HostClient("operator-smoke")
                health = operator.health()
                if health.get("status") != "ready" or not health.get("gameplay_ready"):
                    raise AssertionError(f"Host is not ready: {health}")
                if existing:
                    ensure_player(operator, player_id)
                else:
                    login = operator.login(player_id)
                    if login.get("reused"):
                        raise AssertionError("fresh Host unexpectedly reused player session")

                operator_session = operator.session()
                if not existing and operator_session.get("sequence") != 0:
                    raise AssertionError(f"fresh session sequence must be 0: {operator_session}")
                expected_sequence = int(operator_session["sequence"]) + 1
                event_cursor = operator.events(0, limit=1)["latest_event_id"]

                lab = HostClient("gamelab-smoke")
                state = reset_player_state(lab, player_id) if existing else ensure_player(lab, player_id)
                player = player_from_state(state)
                if float(player["x"]) != 100.0:
                    raise AssertionError(f"P must start at 100, got {player['x']}")

                model = SpineMotorPolicy.fresh(31)
                model.eval()
                history = SensorHistory(
                    sensor_frame(
                        x=player["x"],
                        vx=player["vx"],
                        motor_x=player["motor_x"],
                        target_x=987.0,
                    )
                )
                with torch.no_grad():
                    goal, _ = model.spine(history.tensor())
                    mean, _ = model.motor.parameters_for(
                        goal,
                        motor_state(vx=player["vx"], motor_x=player["motor_x"]),
                    )
                    motor_x = float(torch.tanh(mean).item())

                queued = lab.motor(motor_x)
                if queued.get("sequence") != expected_sequence:
                    raise AssertionError(f"lab command must advance shared sequence: {queued}")

                time.sleep(0.1)
                observed = operator.state()
                observed_player = player_from_state(observed)
                if observed["session"].get("sequence") != expected_sequence:
                    raise AssertionError(f"operator must observe shared sequence: {observed}")

                reset = lab.reset()
                if reset.get("sequence") != expected_sequence:
                    raise AssertionError(f"reset must preserve Host sequence: {reset}")
                deadline = time.monotonic() + 2.0
                reset_state = None
                while time.monotonic() < deadline:
                    reset_state = operator.state()
                    reset_player = player_from_state(reset_state)
                    if (
                        float(reset_player["x"]) == 100.0
                        and float(reset_player["vx"]) == 0.0
                        and float(reset_player["motor_x"]) == 0.0
                    ):
                        break
                    time.sleep(0.01)
                else:
                    raise AssertionError(f"reset did not restore spawn state: {reset_state}")

                if reset_state["session"].get("session_id") != operator_session.get("session_id"):
                    raise AssertionError("reset replaced shared Host session")
                if reset_state["session"].get("sequence") != expected_sequence:
                    raise AssertionError("reset changed shared Host sequence")

                events = operator.events(event_cursor, limit=20).get("events", [])
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

                if (server is not None and server.poll() is not None) or (host is not None and host.poll() is not None):
                    raise AssertionError("backend died during GameLab inference smoke")

                if learned_checkpoint is not None:
                    from gamelab.runtime import load_runtime_model
                    from gamelab.training import verify_spine_policy, verify_recovery_policy
                    learned = load_runtime_model(learned_checkpoint)
                    verification = verify_spine_policy(learned, lab, player_id=player_id)
                    verification["recovery"] = verify_recovery_policy(learned, lab, player_id=player_id)
                    verification["passed"] = verification["passed"] and verification["recovery"]["passed"]
                    if not verification["passed"]:
                        raise AssertionError(f"learned paced verification failed: {verification}")
                    print(f"PASS learned Spine real Host/Zone verification {verification}")

                print(
                    "PASS gamelab shared-Host joystick smoke "
                    f"motor={motor_x:+.3f} moved_x={observed_player['x']} "
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
                    if not existing:
                        try:
                            operator.logout()
                        except Exception:
                            pass
                    operator.close()
                stop_group(host)
                stop_group(server)
                catalog_module.HOST_PORT = saved_port


if __name__ == "__main__":
    raise SystemExit(main())
