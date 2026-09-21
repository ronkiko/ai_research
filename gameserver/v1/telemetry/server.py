"""Passive 120 Hz telemetry observer with one-second authoritative ring."""
from __future__ import annotations

import argparse
from collections import deque
import json
from pathlib import Path
import socket
import threading
from typing import Any

from ..common.config import (HOST, TELEMETRY_QUERY_PORT, TELEMETRY_RING_TICKS,
                             TELEMETRY_UDP_PORT)
from ..common.protocol import ProtocolError, message
from ..common.server import JsonRpcServer


class TelemetryRing:
    def __init__(self, capacity: int = TELEMETRY_RING_TICKS):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._frames: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._last_tick_by_zone: dict[str, int] = {}
        self._lock = threading.RLock()

    def append(self, snapshot: dict[str, Any]) -> None:
        zone_id = snapshot.get("zone_id")
        tick = snapshot.get("world_tick")
        if not isinstance(zone_id, str) or not zone_id:
            raise ProtocolError("snapshot zone_id is invalid")
        if type(tick) is not int or tick < 0:
            raise ProtocolError("snapshot world_tick is invalid")
        entities = snapshot.get("entities")
        if not isinstance(entities, list):
            raise ProtocolError("snapshot entities are invalid")
        with self._lock:
            previous = self._last_tick_by_zone.get(zone_id)
            if previous is not None and tick != previous + 1:
                raise ProtocolError(
                    f"zone {zone_id} tick discontinuity: expected {previous + 1}, got {tick}"
                )
            self._last_tick_by_zone[zone_id] = tick
            self._frames.append(dict(snapshot))

    def latest(self, zone_id: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            for frame in reversed(self._frames):
                if zone_id is None or frame.get("zone_id") == zone_id:
                    return dict(frame)
        return None

    def frames(self, zone_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(frame) for frame in self._frames
                    if zone_id is None or frame.get("zone_id") == zone_id]

    def __len__(self) -> int:
        with self._lock:
            return len(self._frames)


class TelemetryService:
    def __init__(self, *, host: str = HOST, udp_port: int = TELEMETRY_UDP_PORT,
                 query_port: int = TELEMETRY_QUERY_PORT,
                 trace_path: str | Path | None = None):
        self.ring = TelemetryRing()
        self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._udp.bind((host, udp_port))
        self._query = JsonRpcServer(host, query_port, self.dispatch)
        self._query_thread = threading.Thread(target=self._query.serve_forever, daemon=True)
        self.trace_path = Path(trace_path) if trace_path else None
        self._trace = None
        self.validation_errors = 0

    def dispatch(self, request: dict) -> dict:
        kind = request["type"]
        zone_id = request.get("zone_id")
        if zone_id is not None and not isinstance(zone_id, str):
            raise ProtocolError("zone_id must be a string")
        if kind == "latest":
            frame = self.ring.latest(zone_id)
            if frame is None:
                return message("latest", snapshot=None)
            return message("latest", snapshot=frame)
        if kind == "recent":
            return message("recent", snapshots=self.ring.frames(zone_id))
        if kind == "health":
            return message("health", component="telemetry", status="ready",
                           buffered=len(self.ring), validation_errors=self.validation_errors)
        raise ProtocolError(f"unknown Telemetry request: {kind}")

    def _record(self, record: dict[str, Any]) -> None:
        if self._trace is not None:
            self._trace.write(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n")
            self._trace.flush()

    def run(self) -> None:
        if self.trace_path is not None:
            self.trace_path.parent.mkdir(parents=True, exist_ok=True)
            self._trace = self.trace_path.open("a", encoding="utf-8", buffering=1)
        self._query_thread.start()
        print(json.dumps({"component": "telemetry", "status": "READY",
                          "ring_ticks": self.ring.capacity}), flush=True)
        try:
            while True:
                packet, _address = self._udp.recvfrom(1024 * 1024)
                try:
                    snapshot = json.loads(packet.decode("utf-8"))
                    if not isinstance(snapshot, dict) or snapshot.get("type") != "zone_snapshot":
                        raise ProtocolError("invalid zone snapshot")
                    self.ring.append(snapshot)
                    self._record({"type": "snapshot", "valid": True, **snapshot})
                except (UnicodeDecodeError, json.JSONDecodeError, ProtocolError) as exc:
                    self.validation_errors += 1
                    self._record({"type": "validation_error", "valid": False,
                                  "error": str(exc)})
        finally:
            self._query.shutdown()
            self._query.server_close()
            self._udp.close()
            if self._trace is not None:
                self._trace.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="GameServer v1 Telemetry Server")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--udp-port", type=int, default=TELEMETRY_UDP_PORT)
    parser.add_argument("--query-port", type=int, default=TELEMETRY_QUERY_PORT)
    parser.add_argument("--trace", default="gameserver/v1/runtime/telemetry.jsonl")
    args = parser.parse_args()
    TelemetryService(host=args.host, udp_port=args.udp_port,
                     query_port=args.query_port, trace_path=args.trace).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
