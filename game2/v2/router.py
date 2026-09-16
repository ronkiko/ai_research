"""V2 composition/lifecycle entrypoint. Router contains no game rules."""
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

from game2.v2.config import (RuntimeManifest, SessionConfig, allocate_endpoint,
                             new_session_id)
from game2.v2.world.map_loader import load_map


MODULES = {
    "scripted": "game2.v2.controllers.scripted",
}
CRITICAL_MODULES = {"engine", "controller"}
OPTIONAL_MODULES = {"ui", "logger", "monitor"}


def _validate(config: SessionConfig, config_path: Path) -> None:
    if config.controller not in MODULES:
        raise ValueError(f"Unknown controller module: {config.controller}")
    if config.enable_ui:
        raise ValueError("UI is intentionally unavailable in the first V2 patch")
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


def run_session(config_path: str | Path) -> tuple[int, dict]:
    config_path = Path(config_path).resolve()
    config = SessionConfig.from_file(config_path)
    _validate(config, config_path)
    session_id = new_session_id()
    run_dir = Path(__file__).resolve().parent / "runs" / session_id
    run_dir.mkdir(parents=True, exist_ok=False)
    manifest = RuntimeManifest(
        session_id=session_id,
        control=allocate_endpoint(),
        state=allocate_endpoint() if config.enable_state else None,
        telemetry=allocate_endpoint() if config.enable_telemetry else None,
        events=allocate_endpoint() if config.enable_events else None,
        run_dir=str(run_dir),
    )
    manifest_path = run_dir / "manifest.json"
    manifest.write(manifest_path)
    router_log = (run_dir / "router.log").open("w", encoding="utf-8")
    engine_log = (run_dir / "engine.log").open("w", encoding="utf-8")
    controller_log = (run_dir / "controller.log").open("w", encoding="utf-8")
    router_log.write(json.dumps({"session_id": session_id, "manifest": manifest.to_dict()}, sort_keys=True) + "\n")
    router_log.flush()

    engine = controller = None
    pump_thread = None
    interrupted = threading.Event()

    def stop(_signum, _frame):
        interrupted.set()

    previous_int = signal.signal(signal.SIGINT, stop)
    previous_term = signal.signal(signal.SIGTERM, stop)
    status = 1
    summary = {}
    try:
        root = str(Path(__file__).resolve().parents[2])
        engine = subprocess.Popen(
            [sys.executable, "-m", "game2.v2.engine", "--config", str(config_path),
             "--manifest", str(manifest_path)], cwd=root, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
        ready_line = engine.stdout.readline() if engine.stdout else ""
        engine_log.write(ready_line)
        engine_log.flush()
        if not ready_line.startswith("READY "):
            raise RuntimeError("Engine exited or failed before READY")
        pump_thread = threading.Thread(target=_pump, args=(engine.stdout, engine_log), daemon=True)
        pump_thread.start()

        module = MODULES[config.controller]
        controller = subprocess.Popen(
            [sys.executable, "-m", module, "--manifest", str(manifest_path),
             "--ticks", str(config.session_ticks)], cwd=root,
            stdout=controller_log, stderr=subprocess.STDOUT,
        )
        while True:
            if interrupted.is_set():
                _terminate(controller)
                _terminate(engine)
                break
            engine_done = engine.poll() is not None
            controller_done = controller.poll() is not None
            if engine_done:
                if not controller_done:
                    _terminate(controller)
                status = 0 if engine.returncode == 0 else 1
                break
            if controller_done:
                # Engine closes CONTROL after writing SUMMARY, but its final
                # publisher/thread cleanup can still take a moment.
                if (run_dir / "summary.json").exists():
                    try:
                        engine.wait(timeout=5)
                        status = 0 if engine.returncode == 0 else 1
                    except subprocess.TimeoutExpired:
                        _terminate(engine)
                else:
                    _terminate(engine)
                break
            time.sleep(0.01)
        if pump_thread:
            pump_thread.join(timeout=2)
        summary_path = run_dir / "summary.json"
        if summary_path.exists():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        router_log.write(json.dumps({"engine_returncode": engine.returncode,
                                     "controller_returncode": controller.returncode,
                                     "status": status}, sort_keys=True) + "\n")
        router_log.flush()
    except Exception as exc:
        router_log.write(f"ERROR {type(exc).__name__}: {exc}\n")
        router_log.flush()
        _terminate(controller)
        _terminate(engine)
        status = 1
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)
        for handle in (router_log, engine_log, controller_log):
            handle.close()
    return status, summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Compose and supervise Game2 V2 modules")
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    status, summary = run_session(args.config)
    if summary:
        print("session_id=" + summary.get("session_id", "unknown"))
        print("clock=" + summary.get("clock", "unknown"))
        print("ui=off")
        print("episode=" + str(summary.get("episode", "unknown")))
        print("session_ticks=" + str(summary.get("session_ticks", "unknown")))
        print("late=" + str(summary.get("late", "unknown")))
        print("rejected=" + str(summary.get("rejected", "unknown")))
        print("result=" + ("PASS" if status == 0 else "FAIL"))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
