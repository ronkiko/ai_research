"""Standalone versioned scheduler for the embodied multi-zone world mode."""
from __future__ import annotations

import argparse
import json
import os
import threading
import time

from ..common.config import EMBODIED_WORLD_PORT, HOST, PHYSICS_HZ
from ..common.protocol import ProtocolError, message
from ..common.server import JsonRpcServer
from .embodied import EmbodiedWorldRuntime
from .store import WorldCheckpointStore


class EmbodiedWorldService:
    def __init__(
        self,
        *,
        host: str = HOST,
        port: int = EMBODIED_WORLD_PORT,
        physics_hz: int = PHYSICS_HZ,
        state_path: str | None = None,
    ):
        store = WorldCheckpointStore(state_path) if state_path else None
        self.runtime = EmbodiedWorldRuntime(physics_hz=physics_hz, store=store)
        self.server = JsonRpcServer(host, port, self.dispatch)
        self._server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._tick_thread = threading.Thread(target=self._tick_loop, daemon=True)
        self._stop = threading.Event()
        self._started = False

    @property
    def address(self) -> tuple[str, int]:
        host, port = self.server.server_address
        return str(host), int(port)

    def dispatch(self, request: dict) -> dict:
        kind = request["type"]
        if kind == "health":
            snapshot = self.runtime.latest_snapshot()
            return message(
                "health", component="embodied_world", status="ready",
                mode=snapshot["mode"], world_id=snapshot["world_id"],
                world_epoch=snapshot["world_epoch"], world_tick=snapshot["world_tick"],
            )
        if kind == "snapshot":
            return message("snapshot", snapshot=self.runtime.latest_snapshot())
        if kind == "observation":
            entity_id = request.get("entity_id")
            if not isinstance(entity_id, str):
                raise ProtocolError("entity_id is required")
            return message(
                "observation",
                observation=self.runtime.observation(entity_id),
                controller=self.runtime.controller_state(entity_id),
            )
        if kind == "receipt":
            action_id = request.get("action_id")
            if not isinstance(action_id, str):
                raise ProtocolError("action_id is required")
            return message("receipt", receipt=self.runtime.receipt(action_id))
        if kind == "spawn":
            receipt = self.runtime.submit_spawn(
                request_id=str(request.get("request_id", "")),
                entity_id=str(request.get("entity_id", "")),
                embodiment_id=str(request.get("embodiment_id", "")),
                owner_id=str(request.get("owner_id", "")),
                kind=str(request.get("entity_kind", "")),
                zone_id=str(request.get("zone_id", "")),
                spawn_id=str(request.get("spawn_id", "")),
                controller_id=str(request.get("controller_id", "")),
                controller_generation=request.get("controller_generation", 1),
            )
            return message("action", receipt=receipt)
        if kind == "input":
            receipt = self.runtime.submit_input(
                request_id=str(request.get("request_id", "")),
                entity_id=str(request.get("entity_id", "")),
                expected_zone_id=str(request.get("expected_zone_id", "")),
                expected_world_epoch=str(request.get("expected_world_epoch", "")),
                controller_id=str(request.get("controller_id", "")),
                controller_generation=request.get("controller_generation"),
                sequence=request.get("sequence"),
                motor_x=request.get("motor_x"),
            )
            return message("action", receipt=receipt)
        if kind == "day_start":
            receipt = self.runtime.submit_day_start(
                request_id=str(request.get("request_id", "")),
                entity_id=str(request.get("entity_id", "")),
                day_start_id=str(request.get("day_start_id", "")),
                zone_id=str(request.get("zone_id", "hallway")),
                spawn_id=str(request.get("spawn_id", "yuki_day_start")),
                privileged=request.get("capability") == "story_day_start",
            )
            return message("action", receipt=receipt)
        if kind == "setup_reset":
            receipt = self.runtime.submit_setup_reset(
                request_id=str(request.get("request_id", "")),
                entity_id=str(request.get("entity_id", "")),
                episode_id=str(request.get("episode_id", "")),
                reason=str(request.get("reason", "")),
                zone_id=str(request.get("zone_id", "training/flat_run")),
                spawn_id=str(request.get("spawn_id", "training_prepare")),
                x=request.get("x"),
                privileged=request.get("capability") == "training_setup",
            )
            return message("action", receipt=receipt)
        if kind == "transfer":
            raise ProtocolError(
                "direct transfer is unsupported; on_touch portals are applied by physics"
            )
        raise ProtocolError(f"unknown embodied World request: {kind}")

    def _tick_loop(self) -> None:
        period = 1.0 / self.runtime.physics_hz
        next_tick = time.monotonic()
        while not self._stop.is_set():
            self.runtime.tick()
            next_tick += period
            delay = next_tick - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_tick = time.monotonic()

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._server_thread.start()
        self._tick_thread.start()

    def shutdown(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        if self._started:
            self.server.shutdown()
            self.server.server_close()
            self._tick_thread.join(timeout=2)
            self._server_thread.join(timeout=2)
        else:
            self.server.server_close()
        self.runtime.close()

    def run(self) -> None:
        self.start()
        print(json.dumps({
            "component": "embodied_world",
            "status": "READY",
            "mode": "embodied_world_v1",
            "world_id": self.runtime.world_id,
            "physics_hz": self.runtime.physics_hz,
            "host": self.address[0],
            "port": self.address[1],
        }, sort_keys=True), flush=True)
        try:
            self._tick_thread.join()
        finally:
            self.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser(description="GameServer embodied multi-zone world")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=EMBODIED_WORLD_PORT)
    parser.add_argument("--physics-hz", type=int, default=PHYSICS_HZ)
    parser.add_argument(
        "--state",
        default=os.environ.get(
            "EMBODIED_WORLD_STATE",
            "gameserver/v1/runtime/embodied-world.sqlite3",
        ),
    )
    args = parser.parse_args()
    EmbodiedWorldService(
        host=args.host, port=args.port, physics_hz=args.physics_hz, state_path=args.state
    ).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
