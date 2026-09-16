"""Typed operator configuration and non-blocking game2 session orchestration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import json
import math
import os
import tempfile
import shutil
import traceback
from pathlib import Path
import threading
import time
from collections import deque
from typing import Callable

from game import GameContainer
from level import DEFAULT_MAP, load_level


def make_policy(**kwargs):
    from mlp_382 import MLP382Policy

    return MLP382Policy(**kwargs)


def make_runner(*args, **kwargs):
    from mlp_runner import MlpRunner

    return MlpRunner(*args, **kwargs)


ROOT = Path(__file__).parent
SETTINGS_PATH = ROOT / "operator_settings.json"


@dataclass(frozen=True)
class AlgorithmSpec:
    key: str
    label: str
    runner_factory: Callable


@dataclass(frozen=True)
class PolicySpec:
    key: str
    label: str
    factory: Callable
    checkpoint: Path


# Registries intentionally contain implementations only. A future PPO or policy
# can be added here without changing the operator controls.
ALGORITHMS = {"REINFORCE": AlgorithmSpec("REINFORCE", "REINFORCE", make_runner)}
POLICIES = {
    "3-8-2": PolicySpec(
        "3-8-2", "3-8-2", make_policy, ROOT / "models" / "3-8-2" / "weights.pt"
    ),
}


def discover_levels(directory: str | Path | None = None) -> dict[str, Path]:
    """Return valid map files keyed by their filename stem.

    Validation is deliberately delegated to the existing map loader, so the GUI
    never offers a JSON file the physics container cannot load.
    """
    directory = Path(directory) if directory is not None else ROOT / "maps"
    levels = {}
    for path in sorted(directory.glob("*.json")):
        try:
            load_level(path)
        except (OSError, ValueError, TypeError):
            continue
        levels[path.stem] = path
    return levels


@dataclass
class SessionConfig:
    controller: str = "Human"
    bot_mode: str | None = "Play"
    algorithm: str | None = "REINFORCE"
    network: str | None = "3-8-2"
    level: Path = field(default_factory=lambda: DEFAULT_MAP)
    execution: str = "Realtime"
    episodes: int = 100
    checkpoint_mode: str = "Resume"
    auto_speed: float = 100.0
    checkpoint_file: str = ""
    seed: int = 42

    def validate(
        self, *, levels: dict[str, Path] | None = None, require_checkpoint=True
    ) -> None:
        if self.controller not in ("Human", "Bot"):
            raise ValueError("Invalid controller")
        if not isinstance(self.level, (str, Path)):
            raise ValueError("Invalid level")
        level = Path(self.level)
        if not level.exists() or level.suffix != ".json":
            raise ValueError("Level failed validation")
        try:
            load_level(level)
        except (OSError, ValueError, TypeError) as error:
            raise ValueError(f"Level failed validation: {error}") from error
        if levels is not None and level.stem not in levels:
            raise ValueError("Level failed validation")
        if type(self.episodes) is not int or not 1 <= self.episodes <= 1000000:
            raise ValueError("Invalid episode count (1..1000000)")
        if (
            type(self.auto_speed) not in (int, float)
            or not math.isfinite(self.auto_speed)
            or not 0 < self.auto_speed <= 10000
        ):
            raise ValueError("Invalid auto speed (0..10000)")
        if type(self.seed) is not int or not 0 <= self.seed <= 2147483647:
            raise ValueError("Invalid seed (0..2147483647)")
        if not isinstance(self.checkpoint_file, str):
            raise ValueError("Invalid checkpoint path")
        if self.checkpoint_file and Path(self.checkpoint_file).suffix != ".pt":
            raise ValueError("Checkpoint must have .pt extension")
        if self.controller == "Human":
            return
        if self.bot_mode not in ("Play", "Training"):
            raise ValueError("Invalid bot mode")
        if self.algorithm not in ALGORITHMS:
            raise ValueError("Algorithm is not implemented")
        if self.network not in POLICIES:
            raise ValueError("Network is not registered")
        if self.execution not in ("Realtime", "Auto"):
            raise ValueError("Invalid execution mode")
        if self.bot_mode == "Play" and self.execution != "Realtime":
            raise ValueError("Play mode only supports realtime execution")
        if type(self.episodes) is not int or self.episodes < 1:
            raise ValueError("Invalid episode count")
        if self.checkpoint_mode not in ("Resume", "Fresh"):
            raise ValueError("Invalid checkpoint mode")
        if isinstance(self.auto_speed, bool) or not isinstance(
            self.auto_speed, (int, float)
        ):
            raise ValueError("Invalid auto speed")
        if not math.isfinite(self.auto_speed) or self.auto_speed <= 0:
            raise ValueError("Invalid auto speed")
        if (
            require_checkpoint
            and self.bot_mode == "Play"
            and not self.checkpoint_path().is_file()
        ):
            raise ValueError(
                "Веса не найдены. Сначала обучите модель или выберите существующий файл .pt в дополнительных настройках."
            )

    def checkpoint_path(self) -> Path:
        if self.network not in POLICIES:
            raise ValueError("Network is not registered")
        if self.checkpoint_file:
            path = Path(self.checkpoint_file).expanduser()
            return path if path.is_absolute() else ROOT / path
        return POLICIES[self.network].checkpoint


def _config_dict(config: SessionConfig) -> dict:
    values = asdict(config)
    values["level"] = str(config.level)
    return values


def save_settings(config: SessionConfig, path: str | Path = SETTINGS_PATH) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=destination.name + ".", dir=destination.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(_config_dict(config), output, indent=2)
            output.write("\n")
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_settings(path: str | Path = SETTINGS_PATH) -> SessionConfig:
    defaults = SessionConfig()
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("settings must be an object")
        values = _config_dict(defaults)
        for key in values:
            if key in data:
                values[key] = data[key]
        values["level"] = Path(values["level"])
        candidate = SessionConfig(**values)
        candidate.validate(require_checkpoint=False)
        return candidate
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return defaults


class StatusChannel:
    """Bounded structured events with latest-value semantics for live data."""

    def __init__(self):
        self._lock = threading.Lock()
        self._events = deque(maxlen=1024)
        self._live = None
        self._snapshot = None

    def publish(self, event: dict) -> None:
        event = dict(event)
        event_type = event.get("type")
        with self._lock:
            if event_type == "live_stats":
                self._live = event
            elif event_type == "snapshot":
                self._snapshot = event
            elif event_type == "session_started":
                # Do not deliver the previous session's latest values after a
                # new session marker.
                self._live = None
                self._snapshot = None
                self._events.append(event)
            else:
                if event_type == "episode_finished":
                    self._live = None
                self._events.append(event)

    def drain(self) -> list[dict]:
        with self._lock:
            events = list(self._events)
            self._events.clear()
            if self._live is not None:
                events.append(self._live)
                self._live = None
            if self._snapshot is not None:
                events.append(self._snapshot)
                self._snapshot = None
            return events


@dataclass
class Statistics:
    """GUI-facing projection of runner rolling and cumulative metrics."""

    attempts: int = 0
    successes: int = 0
    success_rate_total: float = 0.0
    successes_100: int = 0
    episodes_window: int = 0
    success_rate_100: float = 0.0
    mean_terminal_tick_100: float = 0.0
    last_result: str = ""
    last_tick: int = 0
    jump_requested: int = 0
    jump_applied: int = 0
    late: int = 0
    rejected: int = 0
    episode: int = 0
    tick: int = 0
    cumulative_sim_ticks: int = 0
    speed: float = 1.0
    updated_episodes: int = 0
    last_loss: float = 0.0

    def update(self, event: dict) -> None:
        for key in (
            "attempts",
            "successes",
            "successes_100",
            "episodes_window",
            "jump_requested",
            "jump_applied",
            "late",
            "rejected",
            "episode",
            "tick",
            "cumulative_sim_ticks",
        ):
            if key in event:
                setattr(self, key, int(event[key]))
        for key in (
            "success_rate_total",
            "success_rate_100",
            "mean_terminal_tick_100",
            "speed",
        ):
            if key in event:
                setattr(self, key, float(event[key]))
        if event.get("type") == "episode_finished":
            self.updated_episodes += int(bool(event.get("updated", False)))
            self.last_loss = float(event.get("loss", 0.0))
        if event.get("result") not in (None, "", "running"):
            self.last_result = str(event["result"])
            self.last_tick = int(event.get("tick", 0))
        if "jump_requested" in event:
            self.jump_requested = int(event["jump_requested"])
        if "jump_applied" in event:
            self.jump_applied = int(event["jump_applied"])
        if "late" in event:
            self.late = int(event["late"])
        if "rejected" in event:
            self.rejected = int(event["rejected"])


class SessionController:
    """Own one game runtime and, for Bot sessions, one policy runtime."""

    def __init__(self, status_channel: StatusChannel | None = None):
        self.status_channel = status_channel or StatusChannel()
        self.status = "Stopped"
        self.config = None
        self.game = None
        self.runner = None
        self._stop_event = threading.Event()
        self._game_thread = None
        self._runner_thread = None
        self._preview_at = 0.0
        self._auto_started = 0.0
        self._previous_episode = None
        self._previous_tick = 0
        self._cumulative_sim_ticks = 0
        self._lifecycle_lock = threading.RLock()

    def _publish(self, event_type: str, **payload) -> None:
        self.status_channel.publish(dict(type=event_type, **payload))

    @property
    def running(self):
        return self.status in ("Starting", "Running", "Finishing", "Stopping")

    def apply(self, config: SessionConfig) -> bool:
        with self._lifecycle_lock:
            return self._apply(config)

    def _apply(self, config: SessionConfig) -> bool:
        try:
            config = replace(config, level=Path(config.level))
            if config.controller == "Human":
                config.execution = "Realtime"
            config.validate()
            # Validate first: an invalid draft must not kill a running experiment.
        except (OSError, ValueError, TypeError) as error:
            self._publish("error", message=str(error), details=traceback.format_exc())
            return False
        self.stop()
        self.config = config
        self.runner = None
        self.status = "Starting"
        self._stop_event = threading.Event()
        self._preview_at = 0.0
        self._auto_started = time.monotonic()
        self._previous_episode = None
        self._previous_tick = 0
        self._cumulative_sim_ticks = 0
        mode = "human" if config.controller == "Human" else "mlp"
        auto = (
            config.controller == "Bot"
            and config.bot_mode == "Training"
            and config.execution == "Auto"
        )
        try:
            self.game = GameContainer(
                config.level,
                mode=mode,
                port=0,
                auto=auto,
                window=False,
                external=True,
                snapshot_callback=self._game_snapshot,
            )
        except (OSError, ValueError, RuntimeError) as error:
            self.status = "Error"
            self._publish("error", message=str(error), details=traceback.format_exc())
            return False
        self._publish("session_started", config=_config_dict(config), auto=auto)
        self.status = "Running"
        self._game_thread = threading.Thread(
            target=self._run_game, name="game2-runtime", daemon=True
        )
        self._game_thread.start()
        if config.controller == "Bot":
            self._runner_thread = threading.Thread(
                target=self._run_runner, name="game2-policy", daemon=True
            )
            self._runner_thread.start()
        return True

    def _game_snapshot(self, game, body, metadata):
        now = time.monotonic()
        metadata = dict(metadata)
        episode, tick = int(metadata.get("episode", 0)), int(metadata.get("tick", 0))
        if self._previous_episode is None:
            self._previous_episode = episode
            self._cumulative_sim_ticks += tick
            self._previous_tick = tick
        elif episode != self._previous_episode:
            self._previous_episode = episode
            self._previous_tick = tick
        elif tick >= self._previous_tick:
            self._cumulative_sim_ticks += tick - self._previous_tick
            self._previous_tick = tick
        metadata["cumulative_sim_ticks"] = self._cumulative_sim_ticks
        # Auto observations remain cheap: at most ten wall-clock previews and
        # only the newest one is retained by StatusChannel.
        if self.config is not None and self.config.execution == "Auto":
            if now - self._preview_at < 0.1:
                return
            self._preview_at = now
        if self.config is not None and self.config.execution == "Auto":
            elapsed = max(0.000001, now - self._auto_started)
            hz = int(metadata.get("hz", game.config.hz))
            metadata["speed"] = (self._cumulative_sim_ticks / hz) / elapsed
        else:
            metadata["speed"] = 1.0
        self.status_channel.publish(
            dict(type="snapshot", body=asdict(body), metadata=metadata)
        )

    def _run_game(self):
        game = self.game
        config = self.config
        if game is None or config is None:
            return
        try:
            if config.execution == "Auto":
                game.run_auto(config.auto_speed)
                return
            previous = time.perf_counter()
            next_frame = previous
            period = 1 / (60 if config.controller == "Human" else game.monitor_hz)
            while not self._stop_event.is_set() and not game.quit_requested:
                now = time.perf_counter()
                events = game.advance(now - previous)
                previous = now
                for event in events:
                    self._publish("game_event", **event)
                if now >= next_frame:
                    if config.controller == "Bot":
                        game.present()  # sends the bounded/latest MLP frame
                    self._game_snapshot(game, replace(game.body), game.metadata())
                    next_frame = now + period
                time.sleep(game.config.dt / 4)
        except Exception as error:
            if not self._stop_event.is_set():
                self.status = "Error"
                self._publish(
                    "error", message=str(error), details=traceback.format_exc()
                )
                self._stop_event.set()
                game.quit_requested = True
                if game.transport is not None:
                    game.transport.close()
                elif config.controller == "Human":
                    game.close()

    def _run_runner(self):
        config = self.config
        game = self.game
        if (
            config is None
            or game is None
            or game.transport is None
            or config.network is None
            or config.algorithm is None
        ):
            return
        transport = game.transport
        try:
            from mlp_runner import connect

            algorithm = ALGORITHMS[config.algorithm]
            spec = POLICIES[config.network]
            import torch

            torch.set_num_threads(1)
            policy = spec.factory(seed=config.seed)
            checkpoint = config.checkpoint_path()
            if (
                config.bot_mode == "Training"
                and config.checkpoint_mode == "Fresh"
                and checkpoint.exists()
            ):
                backup = checkpoint.with_name(
                    checkpoint.stem + f".backup-{time.time_ns()}.pt"
                )
                shutil.copy2(checkpoint, backup)
                self._publish(
                    "notice", message=f"Previous weights saved: {backup.name}"
                )
            if config.bot_mode == "Play" or (
                config.checkpoint_mode == "Resume" and checkpoint.exists()
            ):
                policy.load(checkpoint)
            with connect(
                "127.0.0.1",
                transport.address[1],
                timeout=2,
                wait=20,
                stop_event=self._stop_event,
            ) as client:
                self.runner = algorithm.runner_factory(
                    client,
                    policy,
                    training=config.bot_mode == "Training",
                    episodes=config.episodes,
                    checkpoint=checkpoint,
                    max_ticks=600,
                    target_delay=32,
                    hold_ticks=48,
                    save_every=10,
                    console_output=False,
                    event_sink=self.status_channel.publish,
                    stop_event=self._stop_event,
                )
                self.runner.run()
            if not self._stop_event.is_set():
                self.status = "Finishing"
                game.quit_requested = True
        except Exception as error:
            if not self._stop_event.is_set():
                self.status = "Error"
                self._publish(
                    "error", message=str(error), details=traceback.format_exc()
                )
                self._stop_event.set()
                game.quit_requested = True
        finally:
            if self.runner is not None and not self._stop_event.is_set():
                try:
                    self.runner.save_checkpoint()
                    if self.runner.training:
                        self._publish(
                            "checkpoint_saved", path=str(config.checkpoint_path())
                        )
                except Exception as error:
                    self.status = "Error"
                    self._publish("error", message=f"Checkpoint save failed: {error}")
            game.quit_requested = True
            if game.transport is not None:
                game.transport.close()
            if self._game_thread is not None:
                self._game_thread.join(timeout=3)
            if self._game_thread is None or not self._game_thread.is_alive():
                game.close()
            if self.status == "Finishing" and not self._stop_event.is_set():
                self.status = "Finished"
                self._publish("session_finished", status="Finished")

    def set_human_action(self, *, right: bool, jump: bool = False):
        if self.game is not None and self.config and self.config.controller == "Human":
            setter = getattr(self.game.joystick, "set_action", None)
            if setter is not None:
                setter(right=right, jump=jump)

    def restart(self):
        if self.game is not None and self.config and self.config.controller == "Human":
            reset = getattr(self.game.joystick, "request_reset", None)
            if reset is not None:
                reset()

    def stop(self):
        with self._lifecycle_lock:
            self._stop()

    def _stop(self):
        if self.game is None and self.status == "Stopped":
            return
        self.status = "Stopping"
        self._stop_event.set()
        game, runner = self.game, self.runner
        if game is not None:
            game.quit_requested = True
            if game.transport is not None:
                game.transport.close()
        if runner is not None:
            runner.client.close()
        current = threading.current_thread()
        for thread in (self._runner_thread, self._game_thread):
            if thread is not None and thread is not current:
                thread.join(timeout=3)
        if any(
            thread is not None and thread.is_alive()
            for thread in (self._runner_thread, self._game_thread)
        ):
            self.status = "Error"
            raise RuntimeError(
                "Runtime is still shutting down; retry Stop before starting another session"
            )
        save_error = None
        try:
            if runner is not None and runner.training:
                runner.save_checkpoint()
                self._publish(
                    "checkpoint_saved", path=str(self.config.checkpoint_path())
                )
        except Exception as error:
            save_error = error
        finally:
            if game is not None:
                game.close()
            self.game = self.runner = None
            self._game_thread = self._runner_thread = None
            self.status = "Stopped"
            self._publish("session_stopped")
        if save_error is not None:
            self._publish("error", message=f"Checkpoint save failed: {save_error}")

    def exit(self):
        self.stop()
