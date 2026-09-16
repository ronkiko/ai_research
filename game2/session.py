"""Typed operator configuration and non-blocking game2 session orchestration."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import json
import math
from pathlib import Path
import threading
import time
from collections import deque
from typing import Callable

from game import GameContainer
from level import DEFAULT_MAP, load_level
from mlp_382 import MLP382Policy
from mlp_runner import MlpRunner


ROOT = Path(__file__).parent
SETTINGS_PATH = ROOT / 'operator_settings.json'


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
ALGORITHMS = {'REINFORCE': AlgorithmSpec('REINFORCE', 'REINFORCE', MlpRunner)}
POLICIES = {
    '3-8-2': PolicySpec('3-8-2', '3-8-2', MLP382Policy,
                        ROOT / 'models' / '3-8-2' / 'weights.pt'),
}


def discover_levels(directory: str | Path | None = None) -> dict[str, Path]:
    """Return valid map files keyed by their filename stem.

    Validation is deliberately delegated to the existing map loader, so the GUI
    never offers a JSON file the physics container cannot load.
    """
    directory = Path(directory) if directory is not None else ROOT / 'maps'
    levels = {}
    for path in sorted(directory.glob('*.json')):
        try:
            load_level(path)
        except (OSError, ValueError, TypeError):
            continue
        levels[path.stem] = path
    return levels


@dataclass
class SessionConfig:
    controller: str = 'Bot'
    bot_mode: str | None = 'Play'
    algorithm: str | None = 'REINFORCE'
    network: str | None = '3-8-2'
    level: Path = field(default_factory=lambda: DEFAULT_MAP)
    execution: str = 'Realtime'
    episodes: int = 100
    checkpoint_mode: str = 'Resume'
    auto_speed: float = 100.0

    def validate(self, *, levels: dict[str, Path] | None = None) -> None:
        if self.controller not in ('Human', 'Bot'):
            raise ValueError('Invalid controller')
        if not isinstance(self.level, (str, Path)):
            raise ValueError('Invalid level')
        level = Path(self.level)
        if not level.exists() or level.suffix != '.json':
            raise ValueError('Level failed validation')
        try:
            load_level(level)
        except (OSError, ValueError, TypeError) as error:
            raise ValueError(f'Level failed validation: {error}') from error
        if levels is not None and level.stem not in levels:
            raise ValueError('Level failed validation')
        if self.controller == 'Human':
            return
        if self.bot_mode not in ('Play', 'Training'):
            raise ValueError('Invalid bot mode')
        if self.algorithm not in ALGORITHMS:
            raise ValueError('Algorithm is not implemented')
        if self.network not in POLICIES:
            raise ValueError('Network is not registered')
        if self.execution not in ('Realtime', 'Auto'):
            raise ValueError('Invalid execution mode')
        if self.bot_mode == 'Play' and self.execution != 'Realtime':
            raise ValueError('Play mode only supports realtime execution')
        if type(self.episodes) is not int or self.episodes < 1:
            raise ValueError('Invalid episode count')
        if self.checkpoint_mode not in ('Resume', 'Fresh'):
            raise ValueError('Invalid checkpoint mode')
        if isinstance(self.auto_speed, bool) or not isinstance(self.auto_speed, (int, float)):
            raise ValueError('Invalid auto speed')
        if not math.isfinite(self.auto_speed) or self.auto_speed <= 0:
            raise ValueError('Invalid auto speed')
        spec = POLICIES[self.network]
        if self.bot_mode == 'Play' and not spec.checkpoint.exists():
            raise ValueError('Checkpoint not found')

    def checkpoint_path(self) -> Path:
        if self.network not in POLICIES:
            raise ValueError('Network is not registered')
        return POLICIES[self.network].checkpoint


def _config_dict(config: SessionConfig) -> dict:
    values = asdict(config)
    values['level'] = str(config.level)
    return values


def save_settings(config: SessionConfig, path: str | Path = SETTINGS_PATH) -> None:
    destination = Path(path)
    destination.write_text(json.dumps(_config_dict(config), indent=2) + '\n', encoding='utf-8')


def load_settings(path: str | Path = SETTINGS_PATH) -> SessionConfig:
    defaults = SessionConfig()
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError('settings must be an object')
        values = _config_dict(defaults)
        for key in values:
            if key in data:
                values[key] = data[key]
        values['level'] = Path(values['level'])
        candidate = SessionConfig(**values)
        candidate.validate()
        return candidate
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return defaults


class StatusChannel:
    """Bounded structured events with latest-value semantics for live data."""

    def __init__(self):
        self._lock = threading.Lock()
        self._events = deque()
        self._live = None
        self._snapshot = None

    def publish(self, event: dict) -> None:
        event = dict(event)
        event_type = event.get('type')
        with self._lock:
            if event_type == 'live_stats':
                self._live = event
            elif event_type == 'snapshot':
                self._snapshot = event
            elif event_type == 'session_started':
                # Do not deliver the previous session's latest values after a
                # new session marker.
                self._live = None
                self._snapshot = None
                self._events.append(event)
            else:
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
    last_result: str = ''
    last_tick: int = 0
    jump_requested: int = 0
    jump_applied: int = 0
    late: int = 0
    rejected: int = 0
    episode: int = 0
    tick: int = 0
    cumulative_sim_ticks: int = 0
    speed: float = 1.0

    def update(self, event: dict) -> None:
        for key in ('attempts', 'successes', 'successes_100', 'episodes_window',
                    'jump_requested', 'jump_applied', 'late', 'rejected',
                    'episode', 'tick', 'cumulative_sim_ticks'):
            if key in event:
                setattr(self, key, int(event[key]))
        for key in ('success_rate_total', 'success_rate_100', 'mean_terminal_tick_100', 'speed'):
            if key in event:
                setattr(self, key, float(event[key]))
        if 'result' in event:
            self.last_result = str(event['result'])
        if 'tick' in event:
            self.last_tick = int(event['tick'])
        if 'jump_requested' in event:
            self.jump_requested = int(event['jump_requested'])
        if 'jump_applied' in event:
            self.jump_applied = int(event['jump_applied'])
        if 'late' in event:
            self.late = int(event['late'])
        if 'rejected' in event:
            self.rejected = int(event['rejected'])


class SessionController:
    """Own one game runtime and, for Bot sessions, one policy runtime."""

    def __init__(self, status_channel: StatusChannel | None = None):
        self.status_channel = status_channel or StatusChannel()
        self.status = 'Stopped'
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
        return self.status in ('Starting', 'Running', 'Stopping')

    def apply(self, config: SessionConfig) -> bool:
        with self._lifecycle_lock:
            return self._apply(config)

    def _apply(self, config: SessionConfig) -> bool:
        self.stop()
        try:
            config.level = Path(config.level)
            if config.controller == 'Human':
                config.execution = 'Realtime'
            config.validate()
            self.config = replace(config)
        except (OSError, ValueError, TypeError) as error:
            self.status = 'Error'
            self._publish('error', message=str(error))
            return False

        self.status = 'Starting'
        self._stop_event = threading.Event()
        self._preview_at = 0.0
        self._auto_started = time.monotonic()
        self._previous_episode = None
        self._previous_tick = 0
        self._cumulative_sim_ticks = 0
        mode = 'human' if config.controller == 'Human' else 'mlp'
        auto = config.controller == 'Bot' and config.bot_mode == 'Training' \
            and config.execution == 'Auto'
        try:
            self.game = GameContainer(config.level, mode=mode, port=0, auto=auto,
                                      window=False, external=True,
                                      snapshot_callback=self._game_snapshot)
        except (OSError, ValueError, RuntimeError) as error:
            self.status = 'Error'
            self._publish('error', message=str(error))
            return False
        self._publish('session_started', config=_config_dict(config), auto=auto)
        self.status = 'Running'
        self._game_thread = threading.Thread(target=self._run_game, name='game2-runtime',
                                             daemon=True)
        self._game_thread.start()
        if config.controller == 'Bot':
            self._runner_thread = threading.Thread(target=self._run_runner,
                                                   name='game2-policy', daemon=True)
            self._runner_thread.start()
        return True

    def _game_snapshot(self, game, body, metadata):
        now = time.monotonic()
        metadata = dict(metadata)
        episode, tick = int(metadata.get('episode', 0)), int(metadata.get('tick', 0))
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
        metadata['cumulative_sim_ticks'] = self._cumulative_sim_ticks
        # Auto observations remain cheap: at most ten wall-clock previews and
        # only the newest one is retained by StatusChannel.
        if self.config is not None and self.config.execution == 'Auto':
            if now - self._preview_at < 0.1:
                return
            self._preview_at = now
        if self.config is not None and self.config.execution == 'Auto':
            elapsed = max(0.000001, now - self._auto_started)
            hz = int(metadata.get('hz', game.config.hz))
            metadata['speed'] = (self._cumulative_sim_ticks / hz) / elapsed
        else:
            metadata['speed'] = 1.0
        self.status_channel.publish(dict(type='snapshot', body=asdict(body),
                                         metadata=metadata))

    def _run_game(self):
        game = self.game
        config = self.config
        if game is None or config is None:
            return
        try:
            if config.execution == 'Auto':
                game.run_auto(config.auto_speed)
                return
            previous = time.perf_counter()
            next_frame = previous
            period = 1 / (60 if config.controller == 'Human' else game.monitor_hz)
            while not self._stop_event.is_set() and not game.quit_requested:
                now = time.perf_counter()
                events = game.advance(now - previous)
                previous = now
                for event in events:
                    self._publish('game_event', **event)
                if now >= next_frame:
                    if config.controller == 'Bot':
                        game.present()  # sends the bounded/latest MLP frame
                    self._game_snapshot(game, replace(game.body), game.metadata())
                    next_frame = now + period
                time.sleep(game.config.dt / 4)
        except (ConnectionError, OSError, RuntimeError, ValueError) as error:
            if not self._stop_event.is_set():
                self.status = 'Error'
                self._publish('error', message=str(error))
                self._stop_event.set()
                game.quit_requested = True

    def _run_runner(self):
        from mlp_runner import connect

        config = self.config
        game = self.game
        if (config is None or game is None or game.transport is None
                or config.network is None or config.algorithm is None):
            return
        transport = game.transport
        try:
            algorithm = ALGORITHMS[config.algorithm]
            spec = POLICIES[config.network]
            policy = spec.factory()
            checkpoint = spec.checkpoint
            if config.bot_mode == 'Play' or (config.checkpoint_mode == 'Resume' and checkpoint.exists()):
                policy.load(checkpoint)
            with connect('127.0.0.1', transport.address[1], timeout=2, wait=20,
                         stop_event=self._stop_event) as client:
                self.runner = algorithm.runner_factory(
                    client, policy, training=config.bot_mode == 'Training',
                    episodes=config.episodes, checkpoint=checkpoint, max_ticks=600,
                    target_delay=32, hold_ticks=48, save_every=10,
                    event_sink=self.status_channel.publish, stop_event=self._stop_event)
                self.runner.run()
            if not self._stop_event.is_set():
                self.status = 'Finished'
                self._publish('session_finished', status='Finished')
                game.quit_requested = True
        except (ConnectionError, OSError, TimeoutError, ValueError) as error:
            if not self._stop_event.is_set():
                self.status = 'Error'
                self._publish('error', message=str(error))
                self._stop_event.set()
                game.quit_requested = True
        finally:
            if self.runner is not None and not self._stop_event.is_set():
                try:
                    self.runner.save_checkpoint()
                except OSError as error:
                    self.status = 'Error'
                    self._publish('error', message=f'Checkpoint save failed: {error}')

    def set_human_action(self, *, right: bool, jump: bool = False):
        if self.game is not None and self.config and self.config.controller == 'Human':
            setter = getattr(self.game.joystick, 'set_action', None)
            if setter is not None:
                setter(right=right, jump=jump)

    def restart(self):
        if self.game is not None and self.config and self.config.controller == 'Human':
            reset = getattr(self.game.joystick, 'request_reset', None)
            if reset is not None:
                reset()

    def stop(self):
        with self._lifecycle_lock:
            self._stop()

    def _stop(self):
        if self.game is None and self.status == 'Stopped':
            return
        self.status = 'Stopping'
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
        if runner is not None and runner.training:
            runner.save_checkpoint()
        if game is not None:
            game.close()
        self.game = self.runner = None
        self._game_thread = self._runner_thread = None
        self.status = 'Stopped'

    def exit(self):
        self.stop()
