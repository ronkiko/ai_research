"""Deterministic external Player using public Joystick and Vision contracts."""
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
from game2.v2.contracts.vision import VisionFrame
from game2.v2.player.peripherals import VisionReceiver


SOLID = 1
SELF = 3
# The policy still uses the stable class value 3 as its own Actor.
AVATAR = SELF
LOOKAHEAD_PIXELS = 22


@dataclass(frozen=True)
class VisualDecision:
    right: bool
    jump: bool


def _avatar_bounds(frame: VisionFrame) -> tuple[int, int, int, int] | None:
    left = frame.width
    top = frame.height
    right = bottom = -1
    avatar_byte = bytes((SELF,))
    for y in range(frame.height):
        row = frame.pixels[y * frame.width:(y + 1) * frame.width]
        row_left = row.find(avatar_byte)
        if row_left < 0:
            continue
        left = min(left, row_left)
        right = max(right, row.rfind(avatar_byte))
        top = min(top, y)
        bottom = y
    if right < 0:
        return None
    return left, top, right, bottom


def _solid_at(frame: VisionFrame, x: int, y: int) -> bool:
    return (0 <= x < frame.width and 0 <= y < frame.height and
            frame.pixels[y * frame.width + x] == SOLID)


def decide(frame: VisionFrame) -> VisualDecision:
    """Choose a rightward action from one public semantic VisionFrame."""
    if not isinstance(frame, VisionFrame):
        raise TypeError("scripted policy requires a VisionFrame")
    bounds = _avatar_bounds(frame)
    if bounds is None:
        return VisualDecision(right=True, jump=False)

    left, _top, right, bottom = bounds
    foot_y = bottom + 1
    support_now = any(_solid_at(frame, x, foot_y)
                      for x in range(left, right + 1))
    if not support_now:
        return VisualDecision(right=True, jump=False)

    ahead_start = right + 1
    ahead_end = min(frame.width, ahead_start + LOOKAHEAD_PIXELS)
    edge_nearby = any(not _solid_at(frame, x, foot_y)
                      for x in range(ahead_start, ahead_end))
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
    if (not isinstance(message, dict)
            or set(message) != {"version", "type", "sequence", "status"}
            or message.get("version") != 1
            or message.get("type") != "joystick_ack"
            or type(message.get("sequence")) is not int
            or message["sequence"] < 1
            or message.get("status") not in {"accepted", "rejected", "duplicate"}):
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
        # Keep the finite compatibility smoke on its older public manifest.
        manifest = PeripheralManifest.from_file(args.manifest)
    if manifest.vision is None:
        raise ValueError("Scripted Player requires the public Vision capability")
    joystick = _connect(manifest.joystick)
    joystick.settimeout(0.25)
    vision = VisionReceiver(manifest) if manifest.vision is not None else None
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
        if vision is not None:
            vision.connect()
            vision.wait_for_frame(5.0)
        ack_thread = threading.Thread(target=read_acknowledgements,
                                      name="v2-scripted-joystick-acks", daemon=True)
        ack_thread.start()
        print("READY " + json.dumps({"session_id": manifest.session_id,
                                     "vision": vision is not None,
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
            if vision is not None:
                frame = vision.latest
                if frame is not None and frame.world_tick != latest_tick:
                    latest_tick = frame.world_tick
                    # ATTACH exposes Vision before START. Do not emit gameplay
                    # input until this perspective contains SELF.
                    if _avatar_bounds(frame) is not None:
                        decision = decide(frame)
                        if not decision.jump:
                            jump_armed = True
                        elif jump_armed:
                            jump_pending = True
                            jump_armed = False
            if decision is None:
                time.sleep(0.005)
                continue
            # The Player supplies decisions; Controller owns all world scheduling.
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
        if vision is not None:
            vision.close()
    if vision is not None:
        print(f"DIAGNOSTICS vision_frames={vision.frames_received}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
