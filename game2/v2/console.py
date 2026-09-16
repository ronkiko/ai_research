"""Virtual Game2 V2 console composition and lifecycle supervisor."""
from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from game2.v2.config import (InternalManifest, PeripheralManifest, SessionConfig,
                             allocate_endpoint, new_session_id)
from game2.v2.world.map_loader import load_map


MODULES = {"default": "game2.v2.controller"}


def _validate(config: SessionConfig, config_path: Path) -> None:
    if config.controller not in MODULES:
        raise ValueError(f"Unknown controller subsystem: {config.controller}")
    if config.enable_ui:
        raise ValueError("UI is a management-plane placeholder and is not implemented")
    if config.enable_display and not config.enable_state:
        raise ValueError("Display requires the Engine STATE channel")
    load_map(config.map_path(config_path))


def _pump(source, destination):
    try:
        for line in source:
            destination.write(line)
            destination.flush()
    finally:
        source.close()


def _terminate(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        if process is not None:
            process.wait()
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _launch_ready(command, root: str, log, label: str):
    process = subprocess.Popen(command, cwd=root, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, bufsize=1)
    ready_line = process.stdout.readline() if process.stdout else ""
    log.write(ready_line)
    log.flush()
    if not ready_line.startswith("READY "):
        _terminate(process)
        if process.stdout:
            process.stdout.close()
        raise RuntimeError(f"{label} exited or failed before READY")
    threading.Thread(target=_pump, args=(process.stdout, log), daemon=True).start()
    return process


def run_session(config_path: str | Path, launch_player: bool = True) -> tuple[int, dict]:
    """Run a console integration session, optionally attaching an external test Player."""
    config_path = Path(config_path).resolve()
    config = SessionConfig.from_file(config_path)
    _validate(config, config_path)
    session_id = new_session_id()
    run_dir = Path(__file__).resolve().parent / "runs" / session_id
    run_dir.mkdir(parents=True, exist_ok=False)

    internal = InternalManifest(
        session_id=session_id,
        engine_control=allocate_endpoint(),
        engine_state=allocate_endpoint() if config.enable_state else None,
        engine_telemetry=allocate_endpoint() if config.enable_telemetry else None,
        engine_events=allocate_endpoint() if config.enable_events else None,
        run_dir=str(run_dir),
    )
    peripheral = PeripheralManifest(
        session_id=session_id,
        joystick=allocate_endpoint(),
        display=allocate_endpoint() if config.enable_display else None,
    )
    internal_path = run_dir / "internal-manifest.json"
    peripheral_path = run_dir / "peripheral-manifest.json"
    internal.write(internal_path)
    peripheral.write(peripheral_path)

    console_log = (run_dir / "console.log").open("w", encoding="utf-8")
    engine_log = (run_dir / "engine.log").open("w", encoding="utf-8")
    controller_log = (run_dir / "controller.log").open("w", encoding="utf-8")
    display_log = (run_dir / "display.log").open("w", encoding="utf-8") if config.enable_display else None
    player_log = (run_dir / "player.log").open("w", encoding="utf-8") if launch_player else None
    console_log.write(json.dumps({"session_id": session_id,
                                  "internal_manifest": str(internal_path),
                                  "peripheral_manifest": str(peripheral_path)}, sort_keys=True) + "\n")
    console_log.flush()

    engine = controller = display = player = None
    pump_threads = []
    interrupted = threading.Event()

    def stop(_signum, _frame):
        interrupted.set()

    previous_int = signal.signal(signal.SIGINT, stop)
    previous_term = signal.signal(signal.SIGTERM, stop)
    status = 1
    summary = {}
    try:
        root = str(Path(__file__).resolve().parents[2])
        engine = _launch_ready(
            [sys.executable, "-m", "game2.v2.engine", "--config", str(config_path),
             "--manifest", str(internal_path)], root, engine_log, "Engine")
        controller = _launch_ready(
            [sys.executable, "-m", MODULES[config.controller],
             "--internal-manifest", str(internal_path),
             "--peripheral-manifest", str(peripheral_path)],
            root, controller_log, "Controller")
        if config.enable_display:
            display = _launch_ready(
                [sys.executable, "-m", "game2.v2.display",
                 "--internal-manifest", str(internal_path),
                 "--peripheral-manifest", str(peripheral_path)],
                root, display_log, "Display")

        # This is the integration harness boundary: the Console publishes the
        # peripheral manifest, then an independent Player may connect to it.
        console_log.write("READY " + json.dumps(peripheral.to_dict(), sort_keys=True) + "\n")
        console_log.flush()
        if launch_player and engine.poll() is None:
            player = subprocess.Popen(
                [sys.executable, "-m", "game2.v2.players.scripted",
                 "--manifest", str(peripheral_path), "--ticks", str(config.session_ticks)],
                cwd=root, stdout=player_log, stderr=subprocess.STDOUT,
            )

        while True:
            if interrupted.is_set():
                break
            if engine.poll() is not None:
                status = 0 if engine.returncode == 0 else 1
                break
            if player and player.poll() is not None and player.returncode != 0:
                _terminate(engine)
                status = 1
                break
            time.sleep(0.01)
        if interrupted.is_set():
            status = 1
        _terminate(player)
        _terminate(controller)
        _terminate(display)
        _terminate(engine)
        summary_path = run_dir / "summary.json"
        if summary_path.exists():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["display"] = config.enable_display
        console_log.write(json.dumps({"engine_returncode": engine.returncode if engine else None,
                                      "controller_returncode": controller.returncode if controller else None,
                                      "display_returncode": display.returncode if display else None,
                                      "player_returncode": player.returncode if player else None,
                                      "status": status}, sort_keys=True) + "\n")
        console_log.flush()
    except Exception as exc:
        console_log.write(f"ERROR {type(exc).__name__}: {exc}\n")
        console_log.flush()
        for process in (player, display, controller, engine):
            _terminate(process)
        status = 1
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)
        for handle in (console_log, engine_log, controller_log, display_log, player_log):
            if handle:
                handle.close()
    return status, summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Compose and supervise Game2 V2 console subsystems")
    parser.add_argument("--config", required=True)
    parser.add_argument("--scripted-player", action="store_true",
                        help="attach the external scripted Player integration harness")
    args = parser.parse_args(argv)
    status, summary = run_session(args.config, launch_player=args.scripted_player)
    if summary:
        print("session_id=" + summary.get("session_id", "unknown"))
        print("console=ready")
        print("clock=" + summary.get("clock", "unknown"))
        print("display=" + ("on" if summary.get("display") else "off"))
        print("result=" + ("PASS" if status == 0 else "FAIL"))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
