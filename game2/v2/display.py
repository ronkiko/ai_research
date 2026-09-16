"""Independent game-video subsystem. It consumes STATE and never input."""
from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from game2.v2.config import InternalManifest, PeripheralManifest
from game2.v2.protocol import recv_frame
from game2.v2.transport.publisher import LatestPublisher


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
    """Receives authoritative snapshots and counts/render-publishes latest frames."""

    def __init__(self, internal: InternalManifest, peripheral: PeripheralManifest):
        if internal.engine_state is None:
            raise ValueError("Display requires an Engine STATE endpoint")
        self.internal = internal
        self.peripheral = peripheral
        self.frames = 0
        self.output = (LatestPublisher(peripheral.display.host, peripheral.display.port)
                       if peripheral.display else None)
        self.state: socket.socket | None = None

    def run(self) -> int:
        if self.output:
            self.output.start()
        self.state = _connect(self.internal.engine_state)
        print("READY " + json.dumps({"session_id": self.internal.session_id,
                                     "frames": self.frames}, sort_keys=True), flush=True)
        try:
            while True:
                try:
                    snapshot = recv_frame(self.state)
                except socket.timeout:
                    continue
                if snapshot.get("type") != "state":
                    continue
                self.frames += 1
                if self.output:
                    self.output.publish({"version": 1, "type": "video_frame",
                                         "session_id": self.internal.session_id,
                                         "frame": self.frames, "state": snapshot})
        except (EOFError, OSError, socket.timeout, ValueError):
            return 0
        finally:
            if self.state:
                self.state.close()
            if self.output:
                self.output.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Game2 V2 Display subsystem")
    parser.add_argument("--internal-manifest", required=True)
    parser.add_argument("--peripheral-manifest", required=True)
    args = parser.parse_args(argv)
    return DisplayService(InternalManifest.from_file(args.internal_manifest),
                          PeripheralManifest.from_file(args.peripheral_manifest)).run()


if __name__ == "__main__":
    raise SystemExit(main())
