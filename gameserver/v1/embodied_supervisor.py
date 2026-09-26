"""Launch the authoritative embodied world and its Gateway."""
from __future__ import annotations

import fcntl
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

from .common.config import EMBODIED_WORLD_PORT, GATEWAY_PORT, HOST


SERVICES = (
    ("embodied_world", "gameserver.v1.world.embodied_server", EMBODIED_WORLD_PORT),
    ("gateway", "gameserver.v1.gateway.embodied", GATEWAY_PORT),
)


def _wait_port(port: int, process: subprocess.Popen, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"service exited before readiness: {process.args}")
        try:
            with socket.create_connection((HOST, port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"service did not open {HOST}:{port}")


def main() -> int:
    lock_path = Path("gameserver/v1/runtime/embodied-stack.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = lock_path.open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise SystemExit("embodied GameServer stack is already active") from exc

    processes: list[subprocess.Popen] = []
    stopping = False

    def stop(_signum=None, _frame=None):
        nonlocal stopping
        if stopping:
            return
        stopping = True
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        for _name, module, port in SERVICES:
            process = subprocess.Popen([sys.executable, "-m", module])
            processes.append(process)
            _wait_port(port, process)
        print(
            '{"component":"embodied_supervisor","status":"READY",'
            '"mode":"embodied_world_v1"}',
            flush=True,
        )
        while not stopping:
            for process in processes:
                code = process.poll()
                if code is not None:
                    raise RuntimeError(
                        f"service exited unexpectedly: {process.args} code={code}"
                    )
            time.sleep(0.2)
    except KeyboardInterrupt:
        stop()
    finally:
        stop()
        deadline = time.monotonic() + 3.0
        for process in processes:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                process.kill()
        lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
