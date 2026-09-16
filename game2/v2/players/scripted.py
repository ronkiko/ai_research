"""Deterministic external Player using only the public Joystick contract."""
from __future__ import annotations

import argparse
import socket
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from game2.v2.config import PeripheralManifest
from game2.v2.joystick import JoystickState, joystick_message
from game2.v2.protocol import encode_frame, recv_frame


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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Game2 V2 scripted Player")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--ticks", type=int, default=1000)
    args = parser.parse_args(argv)
    manifest = PeripheralManifest.from_file(args.manifest)
    joystick = _connect(manifest.joystick)
    try:
        for sequence in range(1, max(0, args.ticks) + 1):
            # The Player supplies decisions; Controller owns all world scheduling.
            state = JoystickState(sequence, True, sequence == 125)
            joystick.sendall(encode_frame(joystick_message(state)))
            acknowledgement = recv_frame(joystick)
            if (acknowledgement.get("type") != "joystick_ack"
                    or acknowledgement.get("sequence") != sequence
                    or acknowledgement.get("status") != "accepted"):
                return 1
    except (EOFError, OSError, socket.timeout, ValueError):
        return 1
    finally:
        joystick.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
