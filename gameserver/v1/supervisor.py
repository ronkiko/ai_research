"""Launch the independent GameServer v1 service processes."""
from __future__ import annotations

import signal
import socket
import subprocess
import sys
import time

from .common.config import (GATEWAY_PORT, HOST, PERSISTENCE_PORT,
                            TELEMETRY_QUERY_PORT, WORLD_PORT, ZONE_PORT)


SERVICES = (
    ("persistence", "gameserver.v1.persistence.server", PERSISTENCE_PORT),
    ("telemetry", "gameserver.v1.telemetry.server", TELEMETRY_QUERY_PORT),
    ("world", "gameserver.v1.world.server", WORLD_PORT),
    ("zone", "gameserver.v1.zone.server", ZONE_PORT),
    ("mob", "gameserver.v1.mob.server", None),
    ("gateway", "gameserver.v1.gateway.server", GATEWAY_PORT),
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
        for name, module, port in SERVICES:
            process = subprocess.Popen([sys.executable, "-m", module])
            processes.append(process)
            if port is not None:
                _wait_port(port, process)
            else:
                time.sleep(0.1)
                if process.poll() is not None:
                    raise RuntimeError(f"{name} exited during startup")
        print('{"component":"supervisor","status":"READY"}', flush=True)
        while not stopping:
            for process in processes:
                code = process.poll()
                if code is not None:
                    raise RuntimeError(f"service exited unexpectedly: {process.args} code={code}")
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
        for process in processes:
            if process.poll() is None:
                process.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
