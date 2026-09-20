"""Management-only composition of independent Game2 Training processes."""
from __future__ import annotations

import argparse
import json
import queue
from collections import deque
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from game2.v2.contracts.discovery import ConsoleDiscovery
from game2.v2.contracts.framing import recv_frame, send_frame
from game2.v2.contracts.manifests import PlayerManifest
from game2.v2.contracts.screen import ScreenSourceDiscovery
from game2.v2.contracts.screen_server import (
    ScreenServerDiscovery,
    bind_message,
    decode_screen_server_status,
    probe_message,
    unbind_message,
)
from game2.v2.contracts.training_set import TrainingMapSpec, TrainingSetManifest


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SET = ROOT / "game2" / "v2" / "training" / "sets" / "level-1.json"
DEFAULT_CHECKPOINT_DIR = ROOT / "game2" / "v2" / "runtime" / "checkpoints" / "level-1"
DEFAULT_SCREEN_SERVER = ROOT / "game2" / "v2" / "runtime" / "screen-server.json"
DEFAULT_EPISODE_LIMIT = 1200
PROCESS_TIMEOUT = 30.0
SCREEN_REQUEST_TIMEOUT = 3.0
_OUTPUT_END = object()


class TrainingRunError(RuntimeError):
    pass


@dataclass
class ManagedProcess:
    process: subprocess.Popen
    lines: queue.Queue
    reader: threading.Thread
    output_done: threading.Event
    tail: deque[str]

    @classmethod
    def start(cls, command: list[str], *, cwd: Path,
              popen_factory: Callable[..., subprocess.Popen]) -> "ManagedProcess":
        process = popen_factory(
            command,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        lines: queue.Queue = queue.Queue()
        output_done = threading.Event()
        tail: deque[str] = deque(maxlen=20)

        def read_output() -> None:
            try:
                source = getattr(process, "stdout", None)
                if source is not None:
                    try:
                        for line in source:
                            tail.append(line.rstrip())
                            lines.put(line)
                    finally:
                        try:
                            source.close()
                        except (OSError, ValueError):
                            pass
            finally:
                lines.put(_OUTPUT_END)
                output_done.set()

        reader = threading.Thread(
            target=read_output, name="game2-management-training-output", daemon=True
        )
        reader.start()
        return cls(process, lines, reader, output_done, tail)

    def stop(self, timeout: float = 5.0) -> None:
        if self.process.poll() is not None:
            try:
                self.process.wait()
            except (OSError, ValueError):
                pass
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()

    def diagnostic(self) -> str:
        useful = [line for line in self.tail
                  if line and not line.startswith(("READY ", "ATTACHED "))]
        return " | ".join(useful[-4:]) if useful else f"exit={self.process.poll()}"

    def drain(self) -> list[str]:
        result = []
        while True:
            try:
                line = self.lines.get_nowait()
            except queue.Empty:
                return result
            if line is not _OUTPUT_END:
                result.append(line)


def _strict_object(text: str) -> dict[str, Any]:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate field: {key}")
            result[key] = value
        return result

    value = json.loads(text, object_pairs_hook=pairs)
    if not isinstance(value, dict):
        raise ValueError("announcement must be a JSON object")
    return value


def _endpoint_ready(data: dict[str, Any], label: str) -> tuple[str, int]:
    if set(data) != {"host", "port"}:
        raise TrainingRunError(f"{label} READY fields are invalid")
    host, port = data["host"], data["port"]
    if type(host) is not str or not host or type(port) is not int or not 1 <= port <= 65535:
        raise TrainingRunError(f"{label} READY endpoint is invalid")
    return host, port


def _resolve_map(manifest_path: Path, spec: TrainingMapSpec) -> Path:
    path = Path(spec.path)
    return (path if path.is_absolute() else manifest_path.parent / path).resolve()


def _next_log_run(log_root: Path) -> Path:
    log_root.mkdir(parents=True, exist_ok=True)
    indexes = []
    for entry in log_root.iterdir():
        if entry.is_dir() and entry.name.startswith("run-"):
            suffix = entry.name[4:]
            if suffix.isdigit():
                indexes.append(int(suffix))
    run = log_root / f"run-{max(indexes, default=0) + 1:04d}"
    run.mkdir()
    return run


class ScreenControl:
    """Optional Management-side binding to an already-running Screen Server."""

    def __init__(self, screen: int, discovery_path: str | Path = DEFAULT_SCREEN_SERVER):
        if type(screen) is not int or screen <= 0:
            raise ValueError("screen must be a positive integer")
        self.screen = screen
        self.discovery_path = Path(discovery_path)

    def _request(self, message: dict) -> tuple[dict[str, Any], ...]:
        discovery = ScreenServerDiscovery.from_file(self.discovery_path)
        sock = socket.create_connection(
            (discovery.endpoint.host, discovery.endpoint.port),
            timeout=SCREEN_REQUEST_TIMEOUT,
        )
        try:
            sock.settimeout(SCREEN_REQUEST_TIMEOUT)
            send_frame(sock, message)
            response = recv_frame(sock)
            if response.get("type") == "screen_server_error":
                raise TrainingRunError(
                    response.get("message", "Screen Server request failed")
                )
            return decode_screen_server_status(response)
        finally:
            sock.close()

    def preflight(self) -> None:
        status = self._request(probe_message())
        if self.screen > len(status):
            raise TrainingRunError(
                f"Screen #{self.screen} is outside the Screen Server slot range"
            )
        slot = status[self.screen - 1]
        if slot["state"] == "closed":
            raise TrainingRunError(
                f"Screen #{self.screen} is not open; "
                f"run ./game2/v2/op/screen.sh {self.screen} in another terminal"
            )

    def bind(self, source: ScreenSourceDiscovery) -> None:
        self._request(bind_message(self.screen, source))

    def unbind(self) -> None:
        self._request(unbind_message(self.screen))


class TrainingRun:
    """Compose process boundaries without importing their runtime implementations."""

    def __init__(
        self,
        *,
        popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
        sleeper: Callable[[float], None] = time.sleep,
        output=None,
        root: Path = ROOT,
        screen_control_factory: Callable[..., ScreenControl] = ScreenControl,
    ):
        self.popen_factory = popen_factory
        self.sleeper = sleeper
        self.output = output or sys.stdout
        self.root = Path(root)
        self.screen_control_factory = screen_control_factory
        self.processes: list[ManagedProcess] = []
        self.stop_requested = threading.Event()

    def request_stop(self) -> None:
        self.stop_requested.set()

    def _check_stop(self) -> None:
        if self.stop_requested.is_set():
            raise KeyboardInterrupt

    def _write(self, text: str) -> None:
        self.output.write(text + "\n")
        self.output.flush()

    def _spawn(self, command: list[str]) -> ManagedProcess:
        self._check_stop()
        managed = ManagedProcess.start(
            command, cwd=self.root, popen_factory=self.popen_factory
        )
        self.processes.append(managed)
        return managed

    def _stop(self, processes: list[ManagedProcess]) -> None:
        for managed in reversed(processes):
            managed.stop()
            try:
                self.processes.remove(managed)
            except ValueError:
                pass

    def close(self) -> None:
        self._stop(list(self.processes))

    def _announcement(
        self, process: ManagedProcess, prefix: str, timeout: float = PROCESS_TIMEOUT
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            self._check_stop()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TrainingRunError(f"process did not announce {prefix}")
            try:
                line = process.lines.get(timeout=min(remaining, 0.1))
            except queue.Empty:
                if process.process.poll() is not None and process.output_done.is_set():
                    raise TrainingRunError(
                        f"process exited before {prefix}: {process.diagnostic()}"
                    )
                continue
            if line is _OUTPUT_END:
                raise TrainingRunError(
                    f"process exited before {prefix}: {process.diagnostic()}"
                )
            if not isinstance(line, str) or not line.startswith(prefix + " "):
                continue
            try:
                return _strict_object(line[len(prefix) + 1:].strip())
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise TrainingRunError(f"malformed {prefix} announcement") from exc

    @staticmethod
    def _console_config(map_path: Path, episode_limit: int) -> dict[str, Any]:
        return {
            "map": str(map_path),
            "clock_mode": "realtime",
            "physics_hz": 120,
            "controller": "default",
            "enable_display": False,
            "enable_state": True,
            "enable_telemetry": True,
            "enable_events": True,
            "seed": 7,
            "episode_limit": episode_limit,
            "world_ticks": None,
        }

    def _start_console(
        self, directory: Path, map_path: Path, episode_limit: int, view: str,
        trajectory_log: Path,
    ) -> tuple[ManagedProcess, Path, Path]:
        config_path = directory / "console.json"
        discovery_path = directory / "console-discovery.json"
        screen_source_path = directory / "screen-source.json"
        config_path.write_text(
            json.dumps(self._console_config(map_path, episode_limit), sort_keys=True),
            encoding="utf-8",
        )
        console_command = [
            sys.executable, "-m", "game2.v2.console.main", "--server",
            "--config", str(config_path),
            "--discovery", str(discovery_path),
            "--screen-discovery", str(screen_source_path),
            "--screen-view", view,
        ]
        if view == "vision":
            console_command.extend([
                "--screen-trajectory-log", str(trajectory_log),
            ])
        console = self._spawn(console_command)
        try:
            ConsoleDiscovery.from_dict(
                self._announcement(console, "READY", PROCESS_TIMEOUT)
            )
        except (TypeError, ValueError) as exc:
            raise TrainingRunError("Console READY is invalid") from exc
        return console, discovery_path, screen_source_path

    def _wait_screen_source(self, path: Path, timeout: float = 5.0) -> ScreenSourceDiscovery:
        deadline = time.monotonic() + timeout
        last_error = None
        while time.monotonic() < deadline:
            self._check_stop()
            try:
                return ScreenSourceDiscovery.from_file(path)
            except (FileNotFoundError, OSError, ValueError) as exc:
                last_error = exc
                self.sleeper(0.02)
        raise TrainingRunError("Console did not publish ScreenSource") from last_error

    def _run_map(
        self,
        *,
        manifest_path: Path,
        spec: TrainingMapSpec,
        checkpoint_dir: Path,
        fresh: bool,
        max_episodes: int,
        episode_limit: int,
        directory: Path,
        screen_control: ScreenControl | None,
        view: str,
        trajectory_log: Path,
    ) -> bool:
        self._write(f"MAP {spec.map_id}: starting")
        processes: list[ManagedProcess] = []
        screen_bound = False
        try:
            console, discovery_path, screen_source_path = self._start_console(
                directory,
                _resolve_map(manifest_path, spec),
                episode_limit,
                view,
                trajectory_log,
            )
            processes.append(console)

            if screen_control is not None:
                try:
                    screen_control.bind(self._wait_screen_source(screen_source_path))
                    screen_bound = True
                    self._write(
                        f"SCREEN {screen_control.screen}: {spec.map_id} ({view})"
                    )
                except (OSError, TimeoutError, ValueError, TrainingRunError) as exc:
                    self._write(f"SCREEN warning: {exc}; Training continues headless")

            trainer = self._spawn([
                sys.executable, "-m", "game2.v2.training.main",
                "--listen-host", "127.0.0.1", "--listen-port", "0",
                "--mode", "train", "--episodes", str(max_episodes),
                "--stop-on-success",
            ])
            processes.append(trainer)
            trainer_host, trainer_port = _endpoint_ready(
                self._announcement(trainer, "READY"), "Trainer"
            )

            model_command = [
                sys.executable, "-m", "game2.v2.training.model_runtime",
                "--listen-host", "127.0.0.1", "--listen-port", "0",
                "--checkpoint-dir", str(checkpoint_dir),
                "--trajectory-log", str(trajectory_log),
            ]
            if fresh:
                model_command.append("--fresh")
            model = self._spawn(model_command)
            processes.append(model)
            model_host, model_port = _endpoint_ready(
                self._announcement(model, "READY"), "Model"
            )

            player = self._spawn([
                sys.executable, "-m", "game2.v2.player.learned.main",
                "--discovery", str(discovery_path),
                "--model-host", model_host, "--model-port", str(model_port),
                "--trainer-host", trainer_host, "--trainer-port", str(trainer_port),
                "--trajectory-log", str(trajectory_log),
            ])
            processes.append(player)
            try:
                PlayerManifest.from_dict(self._announcement(player, "ATTACHED"))
            except (TypeError, ValueError) as exc:
                raise TrainingRunError("Player ATTACHED is invalid") from exc

            summary = None
            finalization_deadline = None
            while summary is None:
                self._check_stop()
                for line in trainer.drain():
                    if line.startswith("PROGRESS "):
                        self._write(line.rstrip())
                    elif line.startswith("LEARNING "):
                        self._write(line.rstrip())
                    elif line.startswith("EVALUATION "):
                        self._write(line.rstrip())
                    elif line.startswith("SUMMARY "):
                        summary = _strict_object(line[8:].strip())
                if summary is not None:
                    break
                player_exited = player.process.poll() is not None
                trainer_exited = trainer.process.poll() is not None
                model_exited = model.process.poll() is not None
                if model_exited and not player_exited:
                    raise TrainingRunError(
                        "Model exited before Player finalization: " + model.diagnostic()
                    )
                if player_exited or trainer_exited:
                    if finalization_deadline is None:
                        finalization_deadline = time.monotonic() + 5.0
                    for line in trainer.drain():
                        if line.startswith("SUMMARY "):
                            summary = _strict_object(line[8:].strip())
                    if summary is not None:
                        break
                    if trainer_exited and trainer.output_done.is_set():
                        raise TrainingRunError(
                            "Trainer exited without SUMMARY; "
                            "Player: " + player.diagnostic()
                            + "; Trainer: " + trainer.diagnostic()
                            + "; Model: " + model.diagnostic()
                        )
                    if time.monotonic() >= finalization_deadline:
                        raise TrainingRunError(
                            "Training child failed before SUMMARY; "
                            "Player: " + player.diagnostic()
                            + "; Trainer: " + trainer.diagnostic()
                            + "; Model: " + model.diagnostic()
                        )
                self.sleeper(0.005)

            mastered = summary.get("mastered")
            if type(mastered) is not bool:
                raise TrainingRunError("Trainer SUMMARY mastered is invalid")
            self._write(f"MAP {spec.map_id}: {'PASS' if mastered else 'FAIL'}")
            return mastered
        finally:
            if screen_control is not None and screen_bound:
                try:
                    screen_control.unbind()
                except (OSError, TimeoutError, ValueError, TrainingRunError):
                    pass
            self._stop(processes)

    def train(
        self,
        *,
        set_path: str | Path,
        checkpoint_dir: str | Path,
        max_episodes: int,
        fresh: bool,
        episode_limit: int,
        screen: int | None = None,
        screen_server: str | Path = DEFAULT_SCREEN_SERVER,
        view: str = "screen",
        mode: str = "realtime",
        json_output: bool = False,
    ) -> int:
        manifest_path = Path(set_path).expanduser().resolve()
        checkpoint_path = Path(checkpoint_dir).expanduser().resolve()
        if view not in {"screen", "vision"}:
            raise ValueError("view must be screen or vision")
        if mode not in {"realtime", "unpaced"}:
            raise ValueError("mode must be realtime or unpaced")
        if mode == "unpaced" and (screen is not None or view != "screen"):
            raise ValueError("unpaced training is headless; omit --screen and --view vision")
        if mode == "realtime" and view != "screen" and screen is None:
            raise ValueError("--view vision requires --screen")
        manifest = TrainingSetManifest.from_file(manifest_path)

        screen_control = None
        if screen is not None:
            screen_control = self.screen_control_factory(screen, screen_server)
            screen_control.preflight()

        planner = checkpoint_path / "planner.pt"
        motor = checkpoint_path / "motor.pt"
        critic = checkpoint_path / "critic.pt"
        optimizer = checkpoint_path / "optimizer.pt"
        log_root = checkpoint_path / "logs"
        if fresh:
            if log_root.exists():
                shutil.rmtree(log_root)
            self._write("FRESH reset logs")
            removed = []
            for checkpoint in (planner, motor, critic, optimizer):
                if checkpoint.exists():
                    checkpoint.unlink()
                    removed.append(checkpoint.name)
            if removed:
                self._write("FRESH reset checkpoints: " + ", ".join(removed))
        elif (
            not planner.is_file()
            or not motor.is_file()
            or not critic.is_file()
            or not optimizer.is_file()
        ):
            raise TrainingRunError(
                "resume requires planner.pt, motor.pt, critic.pt, and optimizer.pt"
            )
        if type(max_episodes) is not int or max_episodes <= 0:
            raise ValueError("max_episodes must be positive")
        if type(episode_limit) is not int or episode_limit <= 0:
            raise ValueError("episode_limit must be positive")

        if mode == "unpaced":
            from game2.v2.training.unpaced import run_unpaced_training_set

            self._write(
                f"TRAINING SET {manifest.training_set_level}: "
                f"{'fresh' if fresh else 'resume'}, mode=unpaced, headless"
            )
            return run_unpaced_training_set(
                set_path=manifest_path,
                checkpoint_dir=checkpoint_path,
                max_episodes=max_episodes,
                episode_limit=episode_limit,
                fresh=fresh,
                output=self.output,
                should_stop=self.stop_requested.is_set,
                json_output=json_output,
            )

        log_run = _next_log_run(log_root)
        self._write(f"LOG run: {log_run}")

        self._write(
            f"TRAINING SET {manifest.training_set_level}: "
            f"{'fresh' if fresh else 'resume'}"
            + (
                f", screen={screen}, view={view}"
                if screen is not None else ", headless"
            )
        )
        with tempfile.TemporaryDirectory(prefix="game2-v2-training-") as temporary:
            root = Path(temporary)
            for index, spec in enumerate(manifest.training_maps):
                directory = root / f"{index + 1:02d}-{spec.map_id}"
                directory.mkdir()
                passed = self._run_map(
                    manifest_path=manifest_path,
                    spec=spec,
                    checkpoint_dir=checkpoint_path,
                    fresh=(fresh and index == 0),
                    max_episodes=max_episodes,
                    episode_limit=episode_limit,
                    directory=directory,
                    screen_control=screen_control,
                    view=view,
                    trajectory_log=log_run / f"{index + 1:02d}-{spec.map_id}.jsonl",
                )
                if not passed:
                    self._write(f"TRAINING SET {manifest.training_set_level}: FAIL")
                    return 1
                fresh = False
        self._write(f"TRAINING SET {manifest.training_set_level}: PASS")
        return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compose independent Game2 V2 Training processes"
    )
    parser.add_argument("--set", dest="set_path", default=str(DEFAULT_SET))
    parser.add_argument("--checkpoint-dir", default=str(DEFAULT_CHECKPOINT_DIR))
    parser.add_argument("--max-episodes-per-map", type=int, default=50)
    parser.add_argument("--episode-limit", type=int, default=DEFAULT_EPISODE_LIMIT)
    parser.add_argument("--screen", type=int)
    parser.add_argument("--view", choices=("screen", "vision"), default="screen")
    parser.add_argument("--mode", choices=("realtime", "unpaced"), default="realtime")
    parser.add_argument(
        "--json", dest="json_output", action="store_true",
        help="emit structured unpaced training events instead of human output",
    )
    parser.add_argument("--screen-server", default=str(DEFAULT_SCREEN_SERVER))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fresh", action="store_true")
    mode.add_argument("--resume", action="store_true")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    run = TrainingRun()
    previous_int = signal.signal(signal.SIGINT, lambda *_: run.request_stop())
    previous_term = signal.signal(signal.SIGTERM, lambda *_: run.request_stop())
    try:
        return run.train(
            set_path=args.set_path,
            checkpoint_dir=args.checkpoint_dir,
            max_episodes=args.max_episodes_per_map,
            fresh=args.fresh,
            episode_limit=args.episode_limit,
            screen=args.screen,
            screen_server=args.screen_server,
            view=args.view,
            mode=args.mode,
            json_output=args.json_output,
        )
    except KeyboardInterrupt:
        print("Training interrupted", file=sys.stderr, flush=True)
        return 130
    except (
        OSError, RuntimeError, TimeoutError, TypeError, ValueError,
        TrainingRunError,
    ) as exc:
        print(f"ERROR Training failed: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        run.close()
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)


if __name__ == "__main__":
    raise SystemExit(main())
