"""Deterministic external controller used only to prove the process boundary."""
from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from game2.v2.config import RuntimeManifest
from game2.v2.protocol import ActionCommand, action_message, encode_frame, recv_frame


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


def _drain_observer(sock):
    while True:
        try:
            recv_frame(sock)
        except socket.timeout:
            continue
        except (EOFError, OSError, ValueError):
            return


def _current_observation(observers):
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        for sock in observers:
            try:
                message = recv_frame(sock)
            except socket.timeout:
                continue
            except (EOFError, OSError, ValueError):
                continue
            if message.get("type") in {"state", "telemetry"}:
                return message
    return {"episode": 1, "episode_tick": 0}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Game2 V2 scripted controller")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--ticks", type=int, default=1000)
    args = parser.parse_args(argv)
    manifest = RuntimeManifest.from_file(args.manifest)
    control = _connect(manifest.control)
    observers = []
    for endpoint in (manifest.state, manifest.telemetry, manifest.events):
        if endpoint:
            try:
                observers.append(_connect(endpoint))
            except OSError:
                pass
    try:
        observation = _current_observation(observers)
        episode = observation["episode"]
        base_tick = observation.get("episode_tick", observation.get("tick", 0))
        # Build the tape from an observed world tick; Engine startup is not a
        # synchronization point and may already have advanced.
        first_tick = base_tick + 8
        commands = [ActionCommand(episode, 1, first_tick, 124, True, False),
                    ActionCommand(episode, 2, first_tick + 124, 1, True, True),
                    ActionCommand(episode, 3, first_tick + 125,
                                  max(1, args.ticks - first_tick - 125), True, False)]
        for command in commands:
            control.sendall(encode_frame(action_message(command)))
        for observer in observers:
            threading.Thread(target=_drain_observer, args=(observer,), daemon=True).start()
        acknowledgements = {}
        deadline = time.monotonic() + 5.0
        while len(acknowledgements) < len(commands) and time.monotonic() < deadline:
            try:
                ack = recv_frame(control)
            except socket.timeout:
                continue
            except (EOFError, OSError, ValueError):
                break
            if ack.get("type") == "action_ack":
                acknowledgements[ack.get("sequence")] = ack
        if len(acknowledgements) != len(commands) or any(
                acknowledgements[command.sequence].get("status") != "accepted"
                for command in commands):
            return 1
        # CONTROL closes only when Engine has completed or the session shuts down.
        while True:
            try:
                if not control.recv(1):
                    break
            except socket.timeout:
                continue
            except OSError:
                break
    finally:
        for sock in [control, *observers]:
            try:
                sock.close()
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
