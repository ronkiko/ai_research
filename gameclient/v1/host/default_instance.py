"""Ensure the standalone default GameClient Host used by game_v1 MCP."""
from __future__ import annotations

import fcntl
from pathlib import Path
import subprocess
import sys
import time

from ..clients.base import HostClient, HostClientError
from .config import HOST_BIND, HOST_PORT


def _probe() -> bool:
    client = HostClient("game-v1-host-bootstrap", host=HOST_BIND, port=HOST_PORT, timeout=0.2)
    try:
        response = client.health()
        return response.get("component") == "gameclient_host"
    except HostClientError:
        return False
    finally:
        client.close()


def ensure_default_host() -> None:
    if _probe():
        return

    runtime = Path(__file__).resolve().parents[1] / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    lock_path = runtime / "default-host.lock"

    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if _probe():
            return

        log_path = runtime / "default-host.log"
        with log_path.open("ab", buffering=0) as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "gameclient.v1.host.server",
                    "--host",
                    HOST_BIND,
                    "--port",
                    str(HOST_PORT),
                ],
                cwd=Path(__file__).resolve().parents[3],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("default GameClient Host exited during startup")
            if _probe():
                return
            time.sleep(0.05)

        process.terminate()
        raise RuntimeError("default GameClient Host did not become ready")
