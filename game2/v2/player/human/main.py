"""Entrypoint for the external Human Keyboard Player."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from game2.v2.contracts.manifests import PeripheralManifest
from game2.v2.player.human.client import HumanJoystickClient
from game2.v2.player.human.keyboard import KeyboardWindow


INPUT_HZ = 120


def run_player(manifest: PeripheralManifest, *, input_hz: int = INPUT_HZ,
               client_factory=HumanJoystickClient,
               window_factory=KeyboardWindow,
               clock=time.monotonic, sleeper=time.sleep) -> int:
    if type(input_hz) is not int or input_hz <= 0:
        raise ValueError("input_hz must be a positive integer")
    client = client_factory(manifest)
    window = None
    try:
        client.connect()
        window = window_factory()
        interval = 1 / input_hz
        next_input = clock()
        while True:
            if client.failed:
                error = client.error
                print(f"ERROR Human Player transport failed: {error}",
                      file=sys.stderr, flush=True)
                return 1
            if window.poll_close():
                return 0
            state = window.state
            client.send_state(state.right, state.jump)
            window.draw(client.connected)
            next_input += interval
            delay = next_input - clock()
            if delay > 0:
                sleeper(delay)
            else:
                next_input = clock()
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError, ValueError, ConnectionError) as exc:
        print(f"ERROR Human Player failed: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        client.close()
        if window is not None:
            window.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Game2 V2 Human Keyboard Player")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--input-hz", type=int, default=INPUT_HZ)
    args = parser.parse_args(argv)
    try:
        manifest = PeripheralManifest.from_file(args.manifest)
        return run_player(manifest, input_hz=args.input_hz)
    except (OSError, ValueError) as exc:
        print(f"ERROR Human Player startup failed: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
