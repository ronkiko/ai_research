"""Unified process boundary for Game2 V2 Training and Exam runs."""
from __future__ import annotations

import argparse
import json
import math
import os
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .contracts.discovery import ConsoleDiscovery
from .contracts.manifests import PlayerManifest
from .contracts.run_events import encode_event, make_event
from .contracts.training_set import TrainingSetManifest, TrainingMapSpec


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXAM_ROOT = Path.home() / ".local" / "share" / "game2-v2" / "exams"
DEFAULT_EPISODE_LIMIT = 1200
DEFAULT_DELAY = 2.5
PROCESS_TIMEOUT = 30.0
FINALIZATION_TIMEOUT = 5.0
_OUTPUT_END = object()


class RunError(RuntimeError):
    """An operator-facing launcher failure that is safe to report."""


@dataclass
class ManagedProcess:
    process: subprocess.Popen
    lines: queue.Queue
    reader: threading.Thread
    output_done: threading.Event

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
            # Keep children in the launcher's process group. The viewer can
            # therefore terminate the whole owned tree even if the launcher
            # itself has to be force-stopped.
            start_new_session=False,
        )
        lines: queue.Queue = queue.Queue()
        output_done = threading.Event()

        def read_output() -> None:
            try:
                source = getattr(process, "stdout", None)
                if source is not None:
                    try:
                        for line in source:
                            lines.put(line)
                    finally:
                        try:
                            source.close()
                        except (OSError, ValueError):
                            pass
            finally:
                lines.put(_OUTPUT_END)
                output_done.set()

        reader = threading.Thread(target=read_output, name="game2-v2-run-output", daemon=True)
        reader.start()
        return cls(process, lines, reader, output_done)

    def drain_lines(self) -> list[Any]:
        result = []
        while True:
            try:
                line = self.lines.get_nowait()
            except queue.Empty:
                return result
            if line is not _OUTPUT_END:
                result.append(line)

    def wait_output_done(self, timeout: float) -> bool:
        return self.output_done.wait(timeout)

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


def _strict_json(text: str) -> dict[str, Any]:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate process field: {key}")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError(f"invalid process number: {value}")

    value = json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)
    if not isinstance(value, dict):
        raise ValueError("process announcement must be an object")
    return value


def _resolve_map(manifest_path: Path, spec: TrainingMapSpec) -> Path:
    path = Path(spec.path)
    return (path if path.is_absolute() else manifest_path.parent / path).resolve()


def _checkpoint_paths(directory: Path) -> tuple[Path, Path]:
    return directory / "planner.pt", directory / "motor.pt"


def _exam_root(value: str | None) -> Path:
    raw = value or os.environ.get("GAME2_V2_EXAM_ROOT")
    return Path(raw).expanduser() if raw else DEFAULT_EXAM_ROOT


class UnifiedRunner:
    """Own every subprocess in one Training or Exam operation."""

    def __init__(self, *, popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
                 sleeper: Callable[[float], None] = time.sleep,
                 output=None, root: Path = ROOT):
        self.popen_factory = popen_factory
        self.sleeper = sleeper
        self.output = output or sys.stdout
        self.root = Path(root)
        self.processes: list[ManagedProcess] = []
        self.stop_requested = threading.Event()

    def emit(self, event_type: str, **fields: Any) -> dict[str, Any]:
        event = make_event(event_type, **fields)
        self.output.write("EVENT " + encode_event(event) + "\n")
        self.output.flush()
        return event

    def request_stop(self) -> None:
        self.stop_requested.set()

    def _check_stop(self) -> None:
        if self.stop_requested.is_set():
            raise KeyboardInterrupt

    def _spawn(self, command: list[str]) -> ManagedProcess:
        self._check_stop()
        managed = ManagedProcess.start(command, cwd=self.root,
                                       popen_factory=self.popen_factory)
        self.processes.append(managed)
        return managed

    def _discard_processes(self, processes: list[ManagedProcess]) -> None:
        for process in processes:
            try:
                self.processes.remove(process)
            except ValueError:
                pass

    def _stop_processes(self, processes: list[ManagedProcess]) -> None:
        for process in reversed(processes):
            process.stop()
        self._discard_processes(processes)

    @staticmethod
    def _announcement(process: ManagedProcess, prefix: str, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RunError(f"process did not announce {prefix}")
            try:
                line = process.lines.get(timeout=min(remaining, 0.1))
            except queue.Empty:
                if process.process.poll() is not None:
                    if not process.wait_output_done(remaining):
                        raise RunError(f"process output did not finish before {prefix}")
                    continue
                continue
            if line is _OUTPUT_END:
                raise RunError(f"process exited before {prefix}")
            if not isinstance(line, str) or not line.startswith(prefix + " "):
                continue
            try:
                return _strict_json(line[len(prefix) + 1:].strip())
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise RunError(f"malformed {prefix} announcement") from exc

    def _drain(self, process: ManagedProcess) -> list[str]:
        return process.drain_lines()

    def _console_config(self, map_path: Path, clock_mode: str, episode_limit: int) -> dict:
        return {
            "map": str(map_path),
            "clock_mode": clock_mode,
            "physics_hz": 120,
            "controller": "default",
            "enable_display": True,
            "display_mode": "vision",
            "enable_state": True,
            "enable_telemetry": True,
            "enable_events": True,
            "seed": 7,
            "episode_limit": episode_limit,
            "world_ticks": None,
        }

    def _start_console(self, directory: Path, map_path: Path, clock_mode: str,
                       episode_limit: int) -> tuple[ManagedProcess, Path, ConsoleDiscovery]:
        config_path = directory / "console.json"
        discovery_path = directory / "console-discovery.json"
        config_path.write_text(json.dumps(self._console_config(
            map_path, clock_mode, episode_limit), sort_keys=True), encoding="utf-8")
        console = self._spawn([
            sys.executable, "-m", "game2.v2.console.main", "--server",
            "--config", str(config_path), "--discovery", str(discovery_path),
        ])
        announcement = self._announcement(console, "READY", PROCESS_TIMEOUT)
        try:
            discovery = ConsoleDiscovery.from_dict(announcement)
        except (TypeError, ValueError) as exc:
            raise RunError("Console READY is not a public discovery record") from exc
        return console, discovery_path, discovery

    @staticmethod
    def _trainer_ready(data: dict[str, Any]) -> tuple[str, int]:
        if set(data) != {"host", "port"} or type(data["host"]) is not str \
                or not data["host"] or type(data["port"]) is not int \
                or not 1 <= data["port"] <= 65535:
            raise RunError("Trainer READY is malformed")
        return data["host"], data["port"]

    @staticmethod
    def _attached(data: dict[str, Any]) -> PlayerManifest:
        try:
            return PlayerManifest.from_dict(data)
        except (TypeError, ValueError) as exc:
            raise RunError("Player ATTACHED is not a public PlayerManifest") from exc

    @staticmethod
    def _progress(data: dict[str, Any]) -> dict[str, Any]:
        required = {
            "episode_id", "result", "trainable", "updated", "progress", "reward",
            "attempts", "successes",
        }
        if set(data) != required:
            raise RunError("Trainer PROGRESS fields are invalid")
        if type(data["episode_id"]) is not int or data["episode_id"] <= 0 \
                or data["result"] not in {"success", "dead", "timeout"} \
                or type(data["trainable"]) is not bool \
                or type(data["updated"]) is not bool \
                or type(data["progress"]) is bool \
                or not isinstance(data["progress"], (int, float)) \
                or not math.isfinite(float(data["progress"])) \
                or not 0.0 <= float(data["progress"]) <= 1.0 \
                or type(data["reward"]) is bool \
                or not isinstance(data["reward"], (int, float)) \
                or not math.isfinite(float(data["reward"])) \
                or not -1.0 <= float(data["reward"]) <= 1.0 \
                or type(data["attempts"]) is not int or data["attempts"] <= 0 \
                or type(data["successes"]) is not int or data["successes"] < 0 \
                or data["successes"] > data["attempts"]:
            raise RunError("Trainer PROGRESS values are invalid")
        return data

    @staticmethod
    def _result(data: dict[str, Any]) -> dict[str, Any]:
        required = {"session_id", "result", "start_world_tick", "finish_world_tick"}
        if set(data) != required or type(data["session_id"]) is not str \
                or not data["session_id"] or data["result"] not in {"success", "dead", "timeout"} \
                or type(data["start_world_tick"]) is not int or data["start_world_tick"] < 0 \
                or type(data["finish_world_tick"]) is not int \
                or data["finish_world_tick"] < data["start_world_tick"]:
            raise RunError("Player RESULT is malformed")
        return data

    def _sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while True:
            self._check_stop()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self.sleeper(min(remaining, 0.05))

    def _train_map(self, manifest_path: Path, manifest: TrainingSetManifest,
                   spec: TrainingMapSpec, checkpoint_dir: Path, fresh: bool,
                   max_episodes: int, clock_mode: str, episode_limit: int,
                   temp_root: Path) -> bool:
        self.emit("map_started", level=manifest.training_set_level, map_id=spec.map_id)
        processes: list[ManagedProcess] = []
        try:
            console, discovery_path, _discovery = self._start_console(
                temp_root, _resolve_map(manifest_path, spec), clock_mode, episode_limit)
            processes.append(console)
            trainer = self._spawn([
                sys.executable, "-m", "game2.v2.training.main",
                "--listen-host", "127.0.0.1", "--listen-port", "0",
                "--mode", "train", "--episodes", str(max_episodes),
                "--stop-on-success",
            ])
            processes.append(trainer)
            trainer_host, trainer_port = self._trainer_ready(
                self._announcement(trainer, "READY", PROCESS_TIMEOUT))
            player_command = [
                sys.executable, "-m", "game2.v2.player.learned.main",
                "--discovery", str(discovery_path), "--checkpoint-dir", str(checkpoint_dir),
                "--trainer-host", trainer_host, "--trainer-port", str(trainer_port),
            ]
            player_command.append("--fresh" if fresh else "--planner-checkpoint")
            if not fresh:
                planner_path, motor_path = _checkpoint_paths(checkpoint_dir)
                player_command.extend([str(planner_path),
                                       "--motor-checkpoint", str(motor_path)])
            player = self._spawn(player_command)
            processes.append(player)
            player_manifest = self._attached(self._announcement(
                player, "ATTACHED", PROCESS_TIMEOUT))
            self.emit("vision_ready", mode="train", level=manifest.training_set_level,
                      map_id=spec.map_id, player_manifest=player_manifest.to_dict())

            passed = False
            summary = None
            finalization_deadline = None
            while summary is None:
                self._check_stop()
                for line in self._drain(trainer):
                    if line.startswith("PROGRESS "):
                        progress = self._progress(_strict_json(line[9:].strip()))
                        self.emit("map_progress", level=manifest.training_set_level,
                                  map_id=spec.map_id, **progress)
                        passed = passed or (progress["result"] == "success" and
                                            progress["trainable"] and progress["updated"])
                    elif line.startswith("SUMMARY "):
                        summary = _strict_json(line[8:].strip())
                for line in self._drain(player):
                    if line.startswith("ATTACHED "):
                        # The normal path consumes ATTACHED before this loop. A
                        # buffered duplicate is a protocol error, not a second Player.
                        raise RunError("Player emitted duplicate ATTACHED")
                if summary is not None:
                    break
                player_exited = player.process.poll() is not None
                trainer_exited = trainer.process.poll() is not None
                if player_exited or trainer_exited:
                    if finalization_deadline is None:
                        finalization_deadline = time.monotonic() + FINALIZATION_TIMEOUT
                    remaining = finalization_deadline - time.monotonic()
                    if not player.output_done.is_set() and remaining > 0:
                        player.wait_output_done(remaining)
                        continue
                    if not trainer.output_done.is_set() and remaining > 0:
                        trainer.wait_output_done(remaining)
                        continue
                    if trainer_exited:
                        if trainer.output_done.is_set():
                            raise RunError("Trainer exited without SUMMARY")
                    if remaining <= 0:
                        raise RunError("Trainer did not emit SUMMARY after Player exit")
                self.sleeper(0.005)
            if not isinstance(summary, dict):
                raise RunError("Trainer SUMMARY is malformed")
            if passed:
                self.emit("map_passed", level=manifest.training_set_level, map_id=spec.map_id)
                return True
            attempts = summary.get("attempts", 0)
            successes = summary.get("successes", 0)
            if type(attempts) is not int or type(successes) is not int:
                raise RunError("Trainer SUMMARY counters are malformed")
            self.emit("map_failed", level=manifest.training_set_level, map_id=spec.map_id,
                      attempts=attempts, successes=successes)
            return False
        finally:
            self._stop_processes(processes)

    def train(self, *, set_path: str | Path, checkpoint_dir: str | Path,
              max_episodes: int, clock_mode: str, fresh: bool,
              episode_limit: int = DEFAULT_EPISODE_LIMIT) -> int:
        manifest_path = Path(set_path).expanduser().resolve()
        checkpoint_path = Path(checkpoint_dir).expanduser().resolve()
        manifest = TrainingSetManifest.from_file(manifest_path)
        planner_path, motor_path = _checkpoint_paths(checkpoint_path)
        if fresh and (planner_path.exists() or motor_path.exists()):
            raise RunError("fresh Training refuses to overwrite existing checkpoints")
        if not fresh and (not planner_path.is_file() or not motor_path.is_file()):
            raise RunError("resume requires planner.pt and motor.pt")
        if type(max_episodes) is not int or max_episodes <= 0:
            raise ValueError("max_episodes must be positive")
        if clock_mode not in {"realtime", "unpaced"}:
            raise ValueError("clock_mode must be realtime or unpaced")
        if type(episode_limit) is not int or episode_limit <= 0:
            raise ValueError("episode_limit must be positive")

        self.emit("training_set_started", level=manifest.training_set_level)
        with tempfile.TemporaryDirectory(prefix="game2-v2-run-") as temporary:
            temp_root = Path(temporary)
            for index, spec in enumerate(manifest.training_maps):
                if not self._train_map(
                        manifest_path, manifest, spec, checkpoint_path,
                        fresh=(fresh and index == 0), max_episodes=max_episodes,
                        clock_mode=clock_mode, episode_limit=episode_limit,
                        temp_root=temp_root):
                    self.emit("training_set_finished", level=manifest.training_set_level,
                              passed=False)
                    return 1
                fresh = False
        self.emit("training_set_finished", level=manifest.training_set_level, passed=True)
        return 0

    def exam(self, *, set_path: str | Path, checkpoint_dir: str | Path,
             exam_root: str | Path | None, delay: float) -> int:
        manifest_path = Path(set_path).expanduser().resolve()
        checkpoint_path = Path(checkpoint_dir).expanduser().resolve()
        manifest = TrainingSetManifest.from_file(manifest_path)
        planner_path, motor_path = _checkpoint_paths(checkpoint_path)
        if not planner_path.is_file() or not motor_path.is_file():
            raise RunError("Exam requires the completed Training Set checkpoint")
        if type(delay) is bool or not isinstance(delay, (int, float)) \
                or not math.isfinite(float(delay)) or delay <= 0:
            raise ValueError("delay must be a positive finite number")

        root = _exam_root(str(exam_root) if exam_root is not None else None).resolve()
        resource_id = manifest.exam_resource_id
        if Path(resource_id).name != resource_id:
            raise RunError("Exam resource ID is not a safe external resource name")
        resource_path = root / f"{resource_id}.json"
        if not resource_path.is_file():
            self.emit("exam_unavailable", level=manifest.training_set_level,
                      resource_id=resource_id)
            return 1

        self.emit("exam_countdown", level=manifest.training_set_level,
                  resource_id=resource_id, delay_seconds=float(delay))
        self._sleep(float(delay))
        processes: list[ManagedProcess] = []
        try:
            with tempfile.TemporaryDirectory(prefix="game2-v2-exam-") as temporary:
                directory = Path(temporary)
                console, discovery_path, _discovery = self._start_console(
                    directory, resource_path, "realtime", DEFAULT_EPISODE_LIMIT)
                processes.append(console)
                player = self._spawn([
                    sys.executable, "-m", "game2.v2.player.learned.main",
                    "--discovery", str(discovery_path),
                    "--planner-checkpoint", str(planner_path),
                    "--motor-checkpoint", str(motor_path),
                ])
                processes.append(player)
                player_manifest = self._attached(self._announcement(
                    player, "ATTACHED", PROCESS_TIMEOUT))
                self.emit("vision_ready", mode="exam", level=manifest.training_set_level,
                          map_id=resource_id, player_manifest=player_manifest.to_dict())
                self.emit("exam_started", level=manifest.training_set_level,
                          resource_id=resource_id)
                result = None
                while result is None:
                    self._check_stop()
                    for line in self._drain(player):
                        if line.startswith("RESULT "):
                            result = self._result(_strict_json(line[7:].strip()))
                    if result is not None:
                        break
                    if player.process.poll() is not None:
                        if not player.wait_output_done(FINALIZATION_TIMEOUT):
                            raise RunError("Exam Player output did not finish")
                        for line in self._drain(player):
                            if line.startswith("RESULT "):
                                result = self._result(_strict_json(line[7:].strip()))
                        if result is None:
                            raise RunError("Exam Player exited without RESULT")
                        break
                    self.sleeper(0.005)
                outcome = "PASS" if result["result"] == "success" else "FAIL"
                self.emit("exam_finished", level=manifest.training_set_level,
                          resource_id=resource_id, result=outcome)
                return 0
        finally:
            self._stop_processes(processes)

    def close(self) -> None:
        self._stop_processes(list(self.processes))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Game2 V2 unified Training/Exam launcher")
    commands = parser.add_subparsers(dest="command", required=True)

    train = commands.add_parser("train")
    train.add_argument("--set", dest="set_path", required=True)
    train.add_argument("--checkpoint-dir", required=True)
    train.add_argument("--max-episodes-per-map", type=int, default=50)
    train.add_argument("--clock-mode", choices=("realtime", "unpaced"), default="realtime")
    train.add_argument("--episode-limit", type=int, default=DEFAULT_EPISODE_LIMIT)
    freshness = train.add_mutually_exclusive_group(required=True)
    freshness.add_argument("--fresh", action="store_true")
    freshness.add_argument("--resume", action="store_true")

    exam = commands.add_parser("exam")
    exam.add_argument("--set", dest="set_path", required=True)
    exam.add_argument("--checkpoint-dir", required=True)
    exam.add_argument("--exam-root")
    exam.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    runner = UnifiedRunner()
    previous_int = signal.signal(signal.SIGINT, lambda _signum, _frame: runner.request_stop())
    previous_term = signal.signal(signal.SIGTERM, lambda _signum, _frame: runner.request_stop())
    try:
        if args.command == "train":
            return runner.train(
                set_path=args.set_path, checkpoint_dir=args.checkpoint_dir,
                max_episodes=args.max_episodes_per_map, clock_mode=args.clock_mode,
                fresh=args.fresh, episode_limit=args.episode_limit,
            )
        return runner.exam(
            set_path=args.set_path, checkpoint_dir=args.checkpoint_dir,
            exam_root=args.exam_root, delay=args.delay,
        )
    except KeyboardInterrupt:
        return 130
    except (OSError, RuntimeError, TimeoutError, TypeError, ValueError, RunError) as exc:
        try:
            runner.emit("run_failed", message=str(exc) or type(exc).__name__)
        except (OSError, ValueError):
            pass
        return 1
    finally:
        runner.close()
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)


if __name__ == "__main__":
    raise SystemExit(main())
