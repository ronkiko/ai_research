"""120 Hz authoritative one-dimensional Zone Server."""
from __future__ import annotations

import argparse
import json
import socket
import threading
import time

from ..common.config import HOST, PHYSICS_HZ, TELEMETRY_UDP_PORT, ZONE_PORT
from ..common.protocol import ProtocolError, message
from ..common.server import JsonRpcServer
from .model import ZoneRuntime


class ZoneService:
    def __init__(self, *, host: str = HOST, port: int = ZONE_PORT,
                 telemetry_host: str = HOST, telemetry_port: int = TELEMETRY_UDP_PORT,
                 physics_hz: int = PHYSICS_HZ):
        self.runtime = ZoneRuntime(physics_hz=physics_hz)
        self.server = JsonRpcServer(host, port, self.dispatch)
        self._server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._telemetry = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._telemetry_target = (telemetry_host, telemetry_port)
        self._stop = threading.Event()

    def dispatch(self, request: dict) -> dict:
        kind = request["type"]
        if kind == "spawn":
            command_id = self.runtime.enqueue_spawn(
                entity_id=str(request.get("entity_id", "")),
                owner_id=str(request.get("owner_id", "")),
                x=request.get("x", 180.0),
            )
            return message("command_queued", command_id=command_id,
                           world_tick=self.runtime.world_tick)
        if kind == "despawn":
            command_id = self.runtime.enqueue_despawn(str(request.get("entity_id", "")))
            return message("command_queued", command_id=command_id,
                           world_tick=self.runtime.world_tick)
        if kind == "input":
            command_id = self.runtime.enqueue_input(
                entity_id=str(request.get("entity_id", "")),
                sequence=request.get("sequence"),
                move_x=request.get("move_x"),
                source=str(request.get("source", "")),
            )
            return message("command_queued", command_id=command_id,
                           world_tick=self.runtime.world_tick)
        if kind == "snapshot":
            return self.runtime.latest_snapshot()
        if kind == "health":
            return message("health", component="zone", status="ready",
                           world_tick=self.runtime.world_tick)
        raise ProtocolError(f"unknown Zone request: {kind}")

    def publish_snapshot(self, snapshot: dict) -> None:
        try:
            packet = json.dumps(snapshot, separators=(",", ":"), sort_keys=True).encode("utf-8")
            self._telemetry.sendto(packet, self._telemetry_target)
        except OSError:
            # Telemetry is an observer. It must never gate authoritative simulation.
            pass

    def run(self) -> None:
        self._server_thread.start()
        print(json.dumps({"component": "zone", "status": "READY",
                          "physics_hz": self.runtime.physics_hz,
                          "zone_id": self.runtime.zone_id}), flush=True)
        next_tick = time.monotonic()
        period = 1.0 / self.runtime.physics_hz
        try:
            while not self._stop.is_set():
                snapshot = self.runtime.tick()
                self.publish_snapshot(snapshot)
                next_tick += period
                delay = next_tick - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                else:
                    next_tick = time.monotonic()
        finally:
            self.server.shutdown()
            self.server.server_close()
            self._telemetry.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="GameServer v1 Zone Server")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=ZONE_PORT)
    parser.add_argument("--physics-hz", type=int, default=PHYSICS_HZ)
    args = parser.parse_args()
    ZoneService(host=args.host, port=args.port, physics_hz=args.physics_hz).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
