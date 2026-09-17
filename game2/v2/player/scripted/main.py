"""Deterministic external Player using public Joystick and Vision contracts."""
from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from game2.v2.contracts.framing import encode_frame, recv_frame
from game2.v2.contracts.joystick import JoystickState, joystick_message
from game2.v2.contracts.manifests import PeripheralManifest
from game2.v2.contracts.vision import VisionFrame, recv_vision_frame


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


class VisionReceiver:
    """Read the public Vision stream without entering the action loop."""

    def __init__(self, manifest: PeripheralManifest):
        if manifest.vision is None:
            raise ValueError("Vision capability is missing")
        self.manifest = manifest
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self._error: BaseException | None = None
        self.latest: VisionFrame | None = None
        self.frames_received = 0

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._socket is not None and not self._closed.is_set()

    @property
    def failed(self) -> bool:
        with self._lock:
            return self._error is not None

    def connect(self) -> None:
        if self._socket is not None:
            raise RuntimeError("Vision receiver is already connected")
        vision = _connect(self.manifest.vision)
        vision.settimeout(None)
        self._socket = vision
        self._thread = threading.Thread(target=self._read_loop, name="v2-scripted-vision",
                                        daemon=True)
        self._thread.start()

    def _read_loop(self) -> None:
        vision = self._socket
        if vision is None:
            return
        try:
            while not self._closed.is_set():
                frame = recv_vision_frame(vision, self.manifest.session_id)
                with self._lock:
                    self.latest = frame
                    self.frames_received += 1
        except (EOFError, OSError, ValueError) as exc:
            if not self._closed.is_set():
                with self._lock:
                    self._error = exc
        finally:
            with self._lock:
                if self._socket is vision:
                    self._socket = None

    def close(self) -> None:
        self._closed.set()
        with self._lock:
            vision = self._socket
            self._socket = None
        if vision is not None:
            try:
                vision.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                vision.close()
            except OSError:
                pass
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Game2 V2 scripted Player")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--ticks", type=int, default=1000)
    parser.add_argument("--forever", action="store_true",
                        help="keep sending decisions until the public Joystick closes")
    args = parser.parse_args(argv)
    manifest = PeripheralManifest.from_file(args.manifest)
    joystick = _connect(manifest.joystick)
    vision = VisionReceiver(manifest) if manifest.vision is not None else None
    try:
        if vision is not None:
            vision.connect()
        print("READY " + json.dumps({"session_id": manifest.session_id,
                                     "vision": vision is not None}, sort_keys=True), flush=True)
        limit = None if args.forever else max(0, args.ticks)
        sequence = 1
        while limit is None or sequence <= limit:
            # The Player supplies decisions; Controller owns all world scheduling.
            state = JoystickState(sequence, True, sequence == 125)
            joystick.sendall(encode_frame(joystick_message(state)))
            acknowledgement = recv_frame(joystick)
            if (acknowledgement.get("type") != "joystick_ack"
                    or acknowledgement.get("sequence") != sequence
                    or acknowledgement.get("status") not in {"accepted", "rejected", "duplicate"}):
                return 1
            sequence += 1
    except (EOFError, OSError, socket.timeout, ValueError):
        return 1
    finally:
        joystick.close()
        if vision is not None:
            vision.close()
    if vision is not None:
        print(f"DIAGNOSTICS vision_frames={vision.frames_received}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
