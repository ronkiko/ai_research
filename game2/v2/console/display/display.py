"""Independent game-video subsystem. It consumes STATE and never input."""
from __future__ import annotations

import json
import socket
import time

from ..config import DisplayManifest
from ...contracts.framing import recv_frame


def _connect(endpoint, timeout=5.0):
    deadline = time.monotonic() + timeout
    while True:
        try:
            sock = socket.create_connection((endpoint.host, endpoint.port), timeout=1)
            sock.settimeout(0.25)
            return sock
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


class DisplayService:
    """Consumes authoritative snapshots while rendering remains a future patch."""

    def __init__(self, manifest: DisplayManifest):
        self.manifest = manifest
        self.frames_received = 0
        self._latest_state: dict | None = None
        self.state: socket.socket | None = None

    def run(self) -> int:
        self.state = _connect(self.manifest.engine_state)
        print("READY " + json.dumps({"session_id": self.manifest.session_id,
                                     "frames": self.frames_received}, sort_keys=True), flush=True)
        try:
            while True:
                try:
                    snapshot = recv_frame(self.state)
                except socket.timeout:
                    continue
                if snapshot.get("type") != "state":
                    continue
                self.frames_received += 1
                self._latest_state = snapshot
        except (EOFError, OSError, socket.timeout, ValueError):
            return 0
        finally:
            if self.state:
                self.state.close()
            print(f"DIAGNOSTICS frames_received={self.frames_received}", flush=True)
