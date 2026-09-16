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
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from game2.v2.console.config import (ControllerManifest, DisplayManifest, EngineManifest,
                              InternalManifest, SessionConfig,
                              allocate_endpoint, new_session_id)
from game2.v2.contracts.manifests import PeripheralManifest
from game2.v2.console.engine.map_loader import load_map


MODULES = {"default": "game2.v2.console.controller.main"}


def _validate(config: SessionConfig, config_path: Path) -> None:
    if config.controller not in MODULES:
        raise ValueError(f"Unknown controller subsystem: {config.controller}")
    if config.enable_ui:
        raise ValueError("UI is a management-plane placeholder and is not implemented")
    if config.enable_display and not config.enable_state:
        raise ValueError("Display requires the Engine STATE channel")
    if not config.enable_telemetry:
        raise ValueError("Controller requires the Engine TELEMETRY channel")
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


def run_session(config_path: str | Path) -> tuple[int, dict]:
    """Run the Console and its own subsystems, without attaching a Player."""
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
    )
    internal_path = run_dir / "internal-manifest.json"
    engine_manifest_path = run_dir / "engine-manifest.json"
    controller_manifest_path = run_dir / "controller-manifest.json"
    display_manifest_path = run_dir / "display-manifest.json"
    peripheral_path = run_dir / "peripheral-manifest.json"
    internal.write(internal_path)
    EngineManifest(session_id, internal.engine_control, internal.engine_state,
                   internal.engine_telemetry, internal.engine_events,
                   internal.run_dir).write(engine_manifest_path)
    ControllerManifest(session_id, internal.engine_control, internal.engine_telemetry,
                       peripheral.joystick).write(controller_manifest_path)
    if config.enable_display:
        DisplayManifest(session_id, internal.engine_state).write(display_manifest_path)
    peripheral.write(peripheral_path)

    console_log = (run_dir / "console.log").open("w", encoding="utf-8")
    engine_log = (run_dir / "engine.log").open("w", encoding="utf-8")
    controller_log = (run_dir / "controller.log").open("w", encoding="utf-8")
    display_log = (run_dir / "display.log").open("w", encoding="utf-8") if config.enable_display else None
    console_log.write(json.dumps({"session_id": session_id,
                                  "internal_manifest": str(internal_path),
                                  "peripheral_manifest": str(peripheral_path)}, sort_keys=True) + "\n")
    console_log.flush()

    engine = controller = display = None
    interrupted = threading.Event()

    def stop(_signum, _frame):
        interrupted.set()

    previous_int = signal.signal(signal.SIGINT, stop)
    previous_term = signal.signal(signal.SIGTERM, stop)
    status = 1
    summary = {}
    try:
        root = str(Path(__file__).resolve().parents[3])
        engine = _launch_ready(
            [sys.executable, "-m", "game2.v2.console.engine.main", "--config", str(config_path),
             "--manifest", str(engine_manifest_path)], root, engine_log, "Engine")
        controller = _launch_ready(
            [sys.executable, "-m", MODULES[config.controller],
             "--manifest", str(controller_manifest_path)],
            root, controller_log, "Controller")
        if config.enable_display:
            display = _launch_ready(
                [sys.executable, "-m", "game2.v2.console.display.main",
                  "--manifest", str(display_manifest_path)],
                root, display_log, "Display")

        # An external Player may attach only after all Console-owned services are ready.
        ready = {"session_id": session_id, **peripheral.to_dict()}
        console_log.write("READY " + json.dumps(ready, sort_keys=True) + "\n")
        console_log.flush()
        print("READY " + json.dumps(ready, sort_keys=True), flush=True)

        while True:
            if interrupted.is_set():
                break
            if engine.poll() is not None:
                status = 0 if engine.returncode == 0 else 1
                break
            time.sleep(0.01)
        if interrupted.is_set():
            status = 1
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
                                      "status": status}, sort_keys=True) + "\n")
        console_log.flush()
    except Exception as exc:
        console_log.write(f"ERROR {type(exc).__name__}: {exc}\n")
        console_log.flush()
        for process in (display, controller, engine):
            _terminate(process)
        status = 1
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)
        for handle in (console_log, engine_log, controller_log, display_log):
            if handle:
                handle.close()
    return status, summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Compose and supervise Game2 V2 console subsystems")
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    status, summary = run_session(args.config)
    if summary:
        print("session_id=" + summary.get("session_id", "unknown"))
        print("console=ready")
        print("clock=" + summary.get("clock", "unknown"))
        print("display=" + ("on" if summary.get("display") else "off"))
        print("result=" + ("PASS" if status == 0 else "FAIL"))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
