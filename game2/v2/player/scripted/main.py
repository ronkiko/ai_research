"""Deterministic external Player using public Joystick and logical Vision."""
from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from game2.v2.contracts.framing import encode_frame, recv_frame
from game2.v2.contracts.joystick import JoystickState, joystick_message
from game2.v2.contracts.manifests import PeripheralManifest, PlayerManifest
from game2.v2.contracts.vision import META_SELF, PHYSICS_SOLID, VisionGrid
from game2.v2.player.peripherals import VisionReceiver


LOOKAHEAD_TILES = 2


@dataclass(frozen=True)
class VisualDecision:
    right: bool
    jump: bool


def _self_bounds(grid: VisionGrid) -> tuple[int, int, int, int] | None:
    left = grid.metadata_columns
    top = grid.metadata_rows
    right = bottom = -1
    for index, value in enumerate(grid.metadata):
        if not value & META_SELF:
            continue
        y, x = divmod(index, grid.metadata_columns)
        left = min(left, x)
        right = max(right, x)
        top = min(top, y)
        bottom = max(bottom, y)
    if right < 0:
        return None
    return left, top, right, bottom


def _solid_at_sensor(grid: VisionGrid, x: int, y: int) -> bool:
    if not (0 <= x < grid.metadata_columns and 0 <= y < grid.metadata_rows):
        return False
    tile_x = x // grid.subdivisions
    tile_y = y // grid.subdivisions
    return grid.physics[tile_y * grid.columns + tile_x] == PHYSICS_SOLID


def decide(grid: VisionGrid) -> VisualDecision:
    """Choose a rightward action from one public logical VisionGrid."""
    if not isinstance(grid, VisionGrid):
        raise TypeError("scripted policy requires a VisionGrid")
    bounds = _self_bounds(grid)
    if bounds is None:
        return VisualDecision(right=True, jump=False)

    left, _top, right, bottom = bounds
    foot_y = bottom + 1
    support_now = any(
        _solid_at_sensor(grid, x, foot_y) for x in range(left, right + 1)
    )
    if not support_now:
        return VisualDecision(right=True, jump=False)

    ahead_start = right + 1
    ahead_end = min(
        grid.metadata_columns,
        ahead_start + LOOKAHEAD_TILES * grid.subdivisions,
    )
    edge_nearby = any(
        not _solid_at_sensor(grid, x, foot_y)
        for x in range(ahead_start, ahead_end)
    )
    return VisualDecision(right=True, jump=edge_nearby)


def _connect(endpoint, timeout=5.0):
    deadline = time.monotonic() + timeout
    while True:
        try:
            sock = socket.create_connection((endpoint.host, endpoint.port), timeout=1)
            sock.settimeout(1)
            return sock
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


def _validate_acknowledgement(message: dict) -> None:
    if (
        not isinstance(message, dict)
        or set(message) != {"version", "type", "sequence", "status"}
        or message.get("version") != 1
        or message.get("type") != "joystick_ack"
        or type(message.get("sequence")) is not int
        or message["sequence"] < 1
        or message.get("status") not in {"accepted", "rejected", "duplicate"}
    ):
        raise ValueError("invalid joystick acknowledgement")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Game2 V2 scripted Player")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--ticks", type=int, default=1000)
    parser.add_argument("--forever", action="store_true",
                        help="keep sending decisions until the public Joystick closes")
    args = parser.parse_args(argv)
    try:
        manifest = PlayerManifest.from_file(args.manifest)
    except ValueError:
        manifest = PeripheralManifest.from_file(args.manifest)
    if manifest.vision is None:
        raise ValueError("Scripted Player requires the public Vision capability")
    joystick = _connect(manifest.joystick)
    joystick.settimeout(0.25)
    vision = VisionReceiver(manifest)
    ack_stop = threading.Event()
    ack_error: list[BaseException] = []
    ack_thread = None

    def read_acknowledgements() -> None:
        try:
            while not ack_stop.is_set():
                try:
                    _validate_acknowledgement(recv_frame(joystick))
                except socket.timeout:
                    continue
        except (EOFError, OSError, ValueError) as exc:
            if not ack_stop.is_set():
                ack_error.append(exc)

    try:
        vision.connect()
        vision.wait_for_grid(5.0)
        ack_thread = threading.Thread(target=read_acknowledgements,
                                      name="v2-scripted-joystick-acks", daemon=True)
        ack_thread.start()
        print("READY " + json.dumps({"session_id": manifest.session_id,
                                     "vision": True,
                                     "status": "armed"}, sort_keys=True), flush=True)
        limit = None if args.forever else max(0, args.ticks)
        sequence = 1
        next_send = time.monotonic()
        send_period = 1 / 120
        latest_tick = None
        decision = None
        jump_armed = True
        jump_pending = False
        while limit is None or sequence <= limit:
            if ack_error:
                raise ConnectionError("Joystick acknowledgement stream failed") from ack_error[0]
            grid = vision.latest
            if grid is not None and grid.world_tick != latest_tick:
                latest_tick = grid.world_tick
                if _self_bounds(grid) is not None:
                    decision = decide(grid)
                    if not decision.jump:
                        jump_armed = True
                    elif jump_armed:
                        jump_pending = True
                        jump_armed = False
            if decision is None:
                time.sleep(0.005)
                continue
            state = JoystickState(sequence, decision.right, jump_pending)
            joystick.sendall(encode_frame(joystick_message(state)))
            jump_pending = False
            sequence += 1
            next_send += send_period
            delay = next_send - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_send = time.monotonic()
    except (EOFError, OSError, socket.timeout, ValueError, ConnectionError, TimeoutError):
        return 1
    finally:
        ack_stop.set()
        joystick.close()
        if ack_thread is not None and ack_thread is not threading.current_thread():
            ack_thread.join(timeout=1)
        vision.close()
    print(f"DIAGNOSTICS vision_grids={vision.grids_received}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
