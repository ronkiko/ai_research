"""Test-only orchestration for an external ScriptedPlayer and the Console."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from game2.v2.contracts.manifests import PeripheralManifest


ROOT = Path(__file__).resolve().parents[3]


def _wait_for_ready(process: subprocess.Popen) -> tuple[dict, list[str]]:
    if process.stdout is None:
        raise RuntimeError("Console stdout is unavailable")
    deadline = time.monotonic() + 10
    output = []
    while time.monotonic() < deadline:
        line = process.stdout.readline()
        if not line:
            if process.poll() is not None:
                raise RuntimeError("Console exited before READY")
            continue
        output.append(line)
        if line.startswith("READY "):
            return json.loads(line[6:]), output
    raise TimeoutError("Console did not announce READY")


def run_realtime_smoke(config_path: str | Path) -> tuple[int, int, dict]:
    """Start Console and ScriptedPlayer as separate processes."""
    config_path = Path(config_path).resolve()
    console = subprocess.Popen(
        [sys.executable, "-m", "game2.v2.console.main", "--config", str(config_path)],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    player = None
    try:
        ready, _ = _wait_for_ready(console)
        manifest = PeripheralManifest.from_dict(ready)
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "peripheral-manifest.json"
            manifest.write(manifest_path)
            player = subprocess.Popen(
                 [sys.executable, "-m", "game2.v2.player.scripted.main",
                 "--manifest", str(manifest_path), "--ticks", "400"],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True,
            )
            player_status = player.wait(timeout=15)
        console_status = console.wait(timeout=15)
        if console.stdout:
            console.stdout.close()
        if player.stdout:
            player.stdout.close()
        return console_status, player_status, ready
    finally:
        if player is not None and player.poll() is None:
            player.terminate()
            player.wait(timeout=5)
        if console.poll() is None:
            console.terminate()
            console.wait(timeout=5)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Game2 V2 external Player smoke harness")
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    console_status, player_status, _ = run_realtime_smoke(args.config)
    print("console=" + ("PASS" if console_status == 0 else "FAIL"))
    print("external_player=" + ("PASS" if player_status == 0 else "FAIL"))
    return 0 if console_status == 0 and player_status == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
