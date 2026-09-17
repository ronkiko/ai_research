"""Temporary developer wrapper for a playable Game2 V2 session.

The wrapper owns only the external Console and Human Player processes. Console
continues to compose and supervise its private Engine, Controller, and Display.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

from game2.v2.contracts.manifests import PeripheralManifest


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(__file__).resolve().parent / "console" / "configs" / "screen-demo.json"
CONSOLE_MODULE = "game2.v2.console.main"
HUMAN_PLAYER_MODULE = "game2.v2.player.human.main"
CONSOLE_READY_TIMEOUT = 10.0
PLAYER_READY_TIMEOUT = 10.0
# Keep the existing keyword stable; it now means time to receive Player READY.
PLAYER_STARTUP_TIMEOUT = PLAYER_READY_TIMEOUT
_OUTPUT_END = object()


def console_command(python: str | None = None,
                    config_path: str | Path = CONFIG_PATH) -> list[str]:
    """Build the canonical Console command without addressing private services."""
    return [python or sys.executable, "-m", CONSOLE_MODULE,
            "--config", str(Path(config_path).resolve())]


def player_command(manifest_path: str | Path,
                   python: str | None = None) -> list[str]:
    """Build the canonical external Human Player command."""
    return [python or sys.executable, "-m", HUMAN_PLAYER_MODULE,
            "--manifest", str(Path(manifest_path).resolve())]


def launch_console(popen_factory: Callable[..., subprocess.Popen] | None = None,
                   python: str | None = None,
                   config_path: str | Path = CONFIG_PATH,
                   capture_output: bool = False) -> subprocess.Popen:
    """Start Console in its own session so a hard stop can reap descendants."""
    popen = popen_factory or subprocess.Popen
    kwargs = {"cwd": str(ROOT), "start_new_session": True}
    if capture_output:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                      text=True, bufsize=1)
    return popen(console_command(python, config_path), **kwargs)


def launch_player(manifest_path: str | Path,
                  popen_factory: Callable[..., subprocess.Popen] | None = None,
                  python: str | None = None) -> subprocess.Popen:
    """Start Human Player as a separate external process."""
    popen = popen_factory or subprocess.Popen
    return popen(player_command(manifest_path, python), cwd=str(ROOT),
                 start_new_session=True, stdout=subprocess.PIPE,
                 stderr=subprocess.STDOUT, text=True, bufsize=1)


def _strict_ready_json(text: str):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate READY field: {key}")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError(f"invalid READY number: {value}")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)


def parse_console_ready(line: str) -> PeripheralManifest:
    """Validate one public ``READY <json>`` line as a PeripheralManifest."""
    if not isinstance(line, str) or not line.startswith("READY "):
        raise ValueError("Console did not provide a READY line")
    try:
        payload = _strict_ready_json(line[6:])
        return PeripheralManifest.from_dict(payload)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Console READY is not a public PeripheralManifest") from exc


def parse_player_ready(line: str, expected_session_id: str | None = None) -> str:
    """Validate the Human Player READY payload without exposing Console data."""
    if not isinstance(line, str) or not line.startswith("READY "):
        raise ValueError("Human Player did not provide a READY line")
    try:
        payload = _strict_ready_json(line[6:])
        if (not isinstance(payload, dict) or set(payload) != {"session_id"}
                or type(payload["session_id"]) is not str or not payload["session_id"]):
            raise ValueError("Human Player READY fields are invalid")
        if (expected_session_id is not None
                and payload["session_id"] != expected_session_id):
            raise ValueError("Human Player READY session does not match Console")
        return payload["session_id"]
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Human Player READY is malformed") from exc


def _queue_console_output(source, lines: queue.Queue) -> None:
    try:
        for line in source:
            lines.put(line)
    finally:
        lines.put(_OUTPUT_END)


def _pump_queued_output(lines: queue.Queue, destination) -> None:
    while True:
        line = lines.get()
        if line is _OUTPUT_END:
            return
        destination.write(line)
        destination.flush()


def _wait_process_ready(process: subprocess.Popen, timeout: float, parser,
                        label: str, output=None,
                        shutdown_event: threading.Event | None = None):
    """Read one READY line while keeping the child stdout pipe drained."""
    if timeout <= 0:
        raise ValueError(f"{label} READY timeout must be positive")
    source = getattr(process, "stdout", None)
    if source is None:
        raise RuntimeError(f"{label} stdout is unavailable")
    destination = sys.stdout if output is None else output
    lines: queue.Queue = queue.Queue()
    threading.Thread(target=_queue_console_output, args=(source, lines),
                     name=f"v2-demo-{label.lower().replace(' ', '-')}-output",
                     daemon=True).start()
    deadline = time.monotonic() + timeout
    while True:
        if shutdown_event is not None and shutdown_event.is_set():
            raise KeyboardInterrupt
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"{label} did not announce READY")
        try:
            line = lines.get(timeout=min(remaining, 0.1))
        except queue.Empty:
            continue
        if line is _OUTPUT_END:
            raise RuntimeError(f"{label} exited before READY")
        destination.write(line)
        destination.flush()
        if line.startswith("READY"):
            try:
                ready = parser(line)
            except ValueError as exc:
                raise RuntimeError(f"{label} READY is malformed: {exc}") from exc
            threading.Thread(target=_pump_queued_output, args=(lines, destination),
                              name=f"v2-demo-{label.lower().replace(' ', '-')}-output-pump",
                              daemon=True).start()
            return ready


def wait_console_ready(process: subprocess.Popen, timeout: float = CONSOLE_READY_TIMEOUT,
                       output=None, shutdown_event: threading.Event | None = None) -> PeripheralManifest:
    """Read and validate public Console READY while continuously draining stdout."""
    return _wait_process_ready(process, timeout, parse_console_ready, "Console", output,
                               shutdown_event)


def wait_player_ready(process: subprocess.Popen, timeout: float = PLAYER_READY_TIMEOUT,
                      output=None, shutdown_event: threading.Event | None = None,
                      expected_session_id: str | None = None) -> str:
    """Wait for the external Player READY and keep its output visible afterwards."""
    parser = lambda line: parse_player_ready(line, expected_session_id)
    return _wait_process_ready(process, timeout, parser, "Human Player", output,
                               shutdown_event)


def _kill_console_process_group(process: subprocess.Popen) -> None:
    """Kill a top-level process group after graceful termination timed out."""
    pid = getattr(process, "pid", None)
    if type(pid) is not int or pid <= 0:
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        # The process may have exited between wait() and this cleanup.
        pass


def _stop_process(process: subprocess.Popen | None, timeout: float = 5.0) -> int | None:
    """Gracefully stop one top-level process, then reap it after a hard stop."""
    if process is None:
        return None
    if process.poll() is not None:
        return process.wait()
    process.terminate()
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        # kill() makes the expected child lifecycle explicit; killpg() also
        # covers Engine, Controller, or Display if Console is stuck.
        process.kill()
        _kill_console_process_group(process)
        return process.wait()


def stop_console(process: subprocess.Popen | None, timeout: float = 5.0) -> int | None:
    """Gracefully stop Console and its process group."""
    return _stop_process(process, timeout)


def stop_player(process: subprocess.Popen | None, timeout: float = 5.0) -> int | None:
    """Gracefully stop Human Player and its process group."""
    return _stop_process(process, timeout)


def run_demo(*, popen_factory: Callable[..., subprocess.Popen] | None = None,
             shutdown_timeout: float = 5.0,
             poll_interval: float = 1 / 60,
             startup_timeout: float = CONSOLE_READY_TIMEOUT,
             player_startup_timeout: float = PLAYER_STARTUP_TIMEOUT) -> int:
    """Run Console plus Human Player until the operator stops the session."""
    if shutdown_timeout <= 0:
        raise ValueError("shutdown timeout must be positive")
    if poll_interval < 0:
        raise ValueError("poll interval must not be negative")
    if startup_timeout <= 0:
        raise ValueError("startup timeout must be positive")
    if player_startup_timeout <= 0:
        raise ValueError("Player READY timeout must be positive")

    shutdown_requested = threading.Event()

    def request_shutdown(_signum, _frame):
        shutdown_requested.set()

    previous_int = signal.signal(signal.SIGINT, request_shutdown)
    previous_term = signal.signal(signal.SIGTERM, request_shutdown)
    process = None
    player = None
    temporary_directory = None
    status = 1
    try:
        process = launch_console(popen_factory=popen_factory, capture_output=True)
        try:
            manifest = wait_console_ready(process, timeout=startup_timeout,
                                          shutdown_event=shutdown_requested)
        except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
            print(f"ERROR Console startup failed: {exc}", file=sys.stderr, flush=True)
            return 1

        temporary_directory = tempfile.TemporaryDirectory(prefix="game2-v2-demo-")
        manifest_path = Path(temporary_directory.name) / "peripheral-manifest.json"
        try:
            manifest.write(manifest_path)
        except OSError as exc:
            print(f"ERROR Human Player startup failed: {exc}", file=sys.stderr, flush=True)
            return 1
        try:
            player = launch_player(manifest_path, popen_factory=popen_factory)
        except OSError as exc:
            print(f"ERROR Human Player startup failed: {exc}", file=sys.stderr, flush=True)
            return 1
        try:
            wait_player_ready(player, timeout=player_startup_timeout,
                              shutdown_event=shutdown_requested,
                              expected_session_id=manifest.session_id)
        except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
            print(f"ERROR Human Player startup failed: {exc}", file=sys.stderr, flush=True)
            return 1
        player_detached = False

        while True:
            if process.poll() is not None:
                status = process.wait()
                if player is not None and player.poll() is None:
                    stop_player(player, shutdown_timeout)
                break
            if not player_detached and player is not None and player.poll() is not None:
                player_detached = True
                print("Human Player detached", flush=True)
            if shutdown_requested.is_set():
                stop_player(player, shutdown_timeout)
                stop_console(process, shutdown_timeout)
                status = 0
                break
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        # This also covers an interrupted blocking call in a host terminal.
        status = 0
    except BaseException:
        raise
    finally:
        if player is not None and player.poll() is None:
            stop_player(player, shutdown_timeout)
        if process is not None and process.poll() is None:
            stop_console(process, shutdown_timeout)
        for child in (process, player):
            output_stream = getattr(child, "stdout", None) if child is not None else None
            if output_stream is not None:
                output_stream.close()
        if temporary_directory is not None:
            temporary_directory.cleanup()
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)
    return status


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Temporary Game2 V2 gameplay demo")
    parser.add_argument("--shutdown-timeout", type=float, default=5.0,
                        help="seconds to wait for each process before hard kill")
    parser.add_argument("--startup-timeout", type=float, default=CONSOLE_READY_TIMEOUT,
                        help="seconds to wait for public Console READY")
    args = parser.parse_args(argv)
    return run_demo(shutdown_timeout=args.shutdown_timeout,
                    startup_timeout=args.startup_timeout)


if __name__ == "__main__":
    raise SystemExit(main())
