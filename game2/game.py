"""Composition root and lifecycle owner for game2 components."""
import math
import sys
import time

from controls import Action
from joysticks import HumanJoystick
from level import DEFAULT_MAP, load_level
from monitors import AutoMonitor, ColorRenderer, MlpMonitor, WindowMonitor
from physics import PhysicsConfig, PhysicsWorld
from socket_io import SocketTransport
from mlp_joystick import MlpJoystick
from sensors import AutoFeatureProvider


class GameContainer:
    AUTO_SPEED = 100
    AUTO_TARGET_DELAY = 32
    AUTO_MAX_TICKS = 600

    def __init__(self, map_path=DEFAULT_MAP, mode='human', *, port=8765, config=None,
                 monitor_hz=30, window=False, auto=False):
        if mode not in ('human', 'mlp'):
            raise ValueError('mode must be human or mlp')
        if auto and mode != 'mlp':
            raise ValueError('auto mode requires mlp mode')
        if auto and window:
            raise ValueError('auto mode cannot be combined with --window')
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError('port must be an integer in [0, 65535]')
        self.config = config or PhysicsConfig()
        if self.config.hz > 65535:
            raise ValueError('physics Hz exceeds protocol limit')
        if type(monitor_hz) is not int or monitor_hz < 1 or self.config.hz % monitor_hz:
            raise ValueError('monitor_hz must be a positive divisor of physics Hz')
        self.monitor_hz = monitor_hz
        self.mode = mode
        self.auto = bool(auto)
        self.map_path = map_path
        self.level = load_level(map_path)
        self.physics = PhysicsWorld(self.level.new_body(), self.level.surfaces, self.config)
        self.renderer = None if self.auto else ColorRenderer()
        self.feature_provider = AutoFeatureProvider(self.level) if self.auto else None
        self.transport = self.monitor = self.joystick = self.window = None
        self.episode = 1
        self.status = self.event_sequence = self.last_event = 0
        self.jump_requested = self.jump_applied = 0
        self._accumulator = self._discarded = 0.0
        self._frame_key = self._frame = None
        self.closed = self.quit_requested = False
        try:
            if mode == 'human':
                self.monitor = WindowMonitor(self.level)
                self.joystick = HumanJoystick()
            else:
                self.transport = SocketTransport(port=port)
                self.joystick = MlpJoystick(self.transport)
                self.monitor = (AutoMonitor(self.transport) if self.auto
                                else MlpMonitor(self.transport))
                if window:
                    self.window = WindowMonitor(self.level, spectator=True)
        except BaseException:
            self.close()
            raise

    @property
    def body(self):
        return self.physics.body

    @property
    def done(self):
        return self.status != 0

    def reset(self):
        self._check_open()
        # Recreate physics from immutable loaded map; no state leaks between episodes.
        self.physics = PhysicsWorld(self.level.new_body(), self.level.surfaces, self.config)
        self.episode += 1
        self.status = 0
        self.last_event = 3
        self.event_sequence += 1
        self._accumulator = self._discarded = 0.0
        self._frame_key = self._frame = None
        self.joystick.reset()

    def step(self, action):
        """Internal/common one-tick path used by both joysticks and headless tests."""
        self._check_open()
        if not isinstance(action, Action):
            raise ValueError('step requires Action')
        if self.done:
            return []
        was_grounded = self.body.grounded
        events = self.physics.step(move=int(action.right), jump=action.jump)
        if self.mode == 'mlp' and action.jump:
            self.jump_requested += 1
            # Physics applies Jump exactly when the body is supported at input.
            if was_grounded:
                self.jump_applied += 1
        if not self.body.alive:
            self.status = self.last_event = 1
        elif self.level.completed(self.body):
            self.status = self.last_event = 2
            events.append({'event': 'success', 'tick': self.physics.tick})
        if events:
            self.event_sequence += 1
        return [dict(event, episode=self.episode) for event in events]

    def advance(self, elapsed):
        """Real-time accumulator. I/O never blocks here; dt never changes."""
        self._check_open()
        if not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError('elapsed must be finite and non-negative')
        if self.window is not None and self.window.poll_close():
            self.quit_requested = True
            return []
        operation = (self.joystick.poll() if self.mode == 'human'
                     else self.joystick.poll(self.episode, self.physics.tick))
        if operation == 'quit':
            self.quit_requested = True
            return []
        if operation == 'reset':
            self.reset()
            return []
        if self.done:
            return []
        self._discarded += max(0.0, elapsed - 0.25)
        self._accumulator += min(elapsed, 0.25)
        events = []
        while self._accumulator + 1e-12 >= self.config.dt and not self.done:
            if self.mode == 'mlp':
                operation = self.joystick.poll(self.episode, self.physics.tick)
                if operation == 'reset':
                    self.reset()
                    break
                action = self.joystick.next_action(self.physics.tick + 1)
            else:
                action = self.joystick.next_action()
            events.extend(self.step(action))
            self._accumulator = max(0.0, self._accumulator - self.config.dt)
        return events

    def frame(self):
        self._check_open()
        if self.renderer is None:
            raise RuntimeError('auto mode does not build semantic raster frames')
        key = (self.episode, self.physics.tick)
        if key != self._frame_key:
            self._frame = self.renderer.render(self.level.width, self.level.height,
                                               self.level.surfaces, self.body)
            self._frame_key = key
        return self._frame

    def metadata(self):
        return dict(episode=self.episode, tick=self.physics.tick, hz=self.config.hz,
                    monitor_hz=self.monitor_hz, status=self.status,
                    velocity_x=self.body.vx / self.config.max_speed,
                    accepted=getattr(self.joystick, 'accepted', 0),
                    late=getattr(self.joystick, 'late', 0),
                    rejected=getattr(self.joystick, 'rejected', 0),
                    jump_requested=self.jump_requested,
                    jump_applied=self.jump_applied,
                    overrun_ticks=int(self._discarded * self.config.hz),
                    event_sequence=self.event_sequence, last_event=self.last_event)

    def present(self):
        self._check_open()
        frame, metadata = self.frame(), self.metadata()
        if self.mode == 'human':
            self.monitor.present(frame, metadata, self.body)
        else:
            self.monitor.present(frame, metadata)
        if self.window is not None:
            self.window.present(frame, metadata, self.body)

    def run(self):
        """Run the existing wall-clock paced loop."""
        previous = time.perf_counter()
        next_frame = previous
        period = 1 / (60 if self.mode == 'human' else self.monitor_hz)
        if self.transport is not None:
            print(f'game2 socket listening on {self.transport.address[0]}:{self.transport.address[1]}',
                  file=sys.stderr, flush=True)
        while not self.quit_requested:
            now = time.perf_counter()
            events = self.advance(now - previous)
            previous = now
            for event in events:
                print(event, file=sys.stderr, flush=True)
            if now >= next_frame:
                self.present()
                next_frame = now + period
            time.sleep(self.config.dt / 4)

    def run_auto(self, speed=AUTO_SPEED):
        """Run fixed simulation ticks as fast as the controller can safely follow.

        The controller barrier is deliberately at command acceptance, not at the
        command's target tick. This keeps the future-action pipeline intact while
        preventing accelerated simulation from making normal actions late.
        """
        self._check_open()
        if self.mode != 'mlp':
            raise ValueError('auto mode requires mlp mode')
        if not self.auto:
            raise ValueError('auto scheduler requires auto mode')
        if self.window is not None:
            raise ValueError('auto mode cannot be combined with --window')
        if isinstance(speed, bool) or not isinstance(speed, (int, float)):
            raise ValueError('speed must be a positive finite number')
        if not math.isfinite(speed) or speed <= 0:
            raise ValueError('speed must be a positive finite number')

        interval = self.config.hz // self.monitor_hz
        started = time.perf_counter()
        simulated_ticks = 0
        self._auto_metrics = dict(observations=0, feature_build_ms=0.0,
                                  transport_wait_ms=0.0, physics_ms=0.0,
                                  mlp_wait_ms=0.0)
        try:
            if self.transport is not None:
                print(f'game2 socket listening on {self.transport.address[0]}:'
                      f'{self.transport.address[1]}', file=sys.stderr, flush=True)
            if self._auto_wait_for_connection() == 'quit':
                return
            while not self.quit_requested:
                self._publish_observation()
                if self.done:
                    wait_started = time.perf_counter()
                    operation = self._auto_wait_for_reset()
                    self._auto_metrics['mlp_wait_ms'] += (time.perf_counter() - wait_started) * 1000
                    if operation == 'quit':
                        break
                    self.reset()
                    continue

                next_sequence = self.joystick.accepted + 1
                pending = [(next_sequence, self.physics.tick + self.AUTO_TARGET_DELAY)]
                next_sequence += 1
                reset_requested = False
                while not self.done and not self.quit_requested:
                    for _ in range(interval):
                        if self.quit_requested:
                            break
                        operation = self.joystick.poll(self.episode, self.physics.tick)
                        if operation == 'quit':
                            self.quit_requested = True
                            break
                        if operation == 'reset':
                            self.reset()
                            reset_requested = True
                            break
                        next_tick = self.physics.tick + 1
                        while pending and pending[0][1] <= next_tick:
                            wait_started = time.perf_counter()
                            operation = self._auto_wait_for_acceptance(pending[0][0] - 1)
                            self._auto_metrics['mlp_wait_ms'] += (time.perf_counter() - wait_started) * 1000
                            if operation == 'quit':
                                break
                            if operation == 'reset':
                                self.reset()
                                reset_requested = True
                                break
                            pending.pop(0)
                        if reset_requested or self.quit_requested or operation == 'quit':
                            break
                        self._auto_pace(started, simulated_ticks + 1, speed)
                        physics_started = time.perf_counter()
                        events = self.step(self.joystick.next_action(self.physics.tick + 1))
                        self._auto_metrics['physics_ms'] += (time.perf_counter() - physics_started) * 1000
                        simulated_ticks += 1
                        for event in events:
                            print(event, file=sys.stderr, flush=True)
                        if self.done:
                            break
                    if reset_requested or self.quit_requested or self.done:
                        break
                    self._publish_observation()
                    if self.physics.tick >= self.AUTO_MAX_TICKS:
                        wait_started = time.perf_counter()
                        operation = self._auto_wait_for_reset()
                        self._auto_metrics['mlp_wait_ms'] += (time.perf_counter() - wait_started) * 1000
                        if operation == 'quit':
                            break
                        self.reset()
                        reset_requested = True
                        break
                    pending.append((next_sequence,
                                    self.physics.tick + self.AUTO_TARGET_DELAY))
                    next_sequence += 1
                # A terminal frame is published before waiting for the runner's
                # reset. For a running game it was already published above.
        finally:
            wall_seconds = time.perf_counter() - started
            simulated_seconds = simulated_ticks / self.config.hz
            effective_speed = (simulated_seconds / wall_seconds
                               if wall_seconds > 0 else 0.0)
            print(f'auto_summary simulated_seconds={simulated_seconds:.6f} '
                  f'wall_seconds={wall_seconds:.6f} '
                  f'effective_speed_x={effective_speed:.2f} '
                  f'observations={getattr(self, "_auto_metrics", {}).get("observations", 0)} '
                  f'physics_ticks={simulated_ticks} late={getattr(self.joystick, "late", 0)} '
                  f'rejected={getattr(self.joystick, "rejected", 0)} '
                  f'feature_build_ms={getattr(self, "_auto_metrics", {}).get("feature_build_ms", 0):.3f} '
                  f'transport_wait_ms={getattr(self, "_auto_metrics", {}).get("transport_wait_ms", 0):.3f} '
                  f'physics_ms={getattr(self, "_auto_metrics", {}).get("physics_ms", 0):.3f} '
                  f'mlp_wait_ms={getattr(self, "_auto_metrics", {}).get("mlp_wait_ms", 0):.3f}',
                  file=sys.stderr, flush=True)

    def _publish_observation(self):
        started = time.perf_counter()
        reading = self.feature_provider.read(self.body, self.body.vx / self.config.max_speed)
        self._auto_metrics['feature_build_ms'] += (time.perf_counter() - started) * 1000
        self._auto_metrics['observations'] += 1
        self.monitor.present(reading, self.metadata())

    def _auto_wait_for_connection(self):
        while not self.quit_requested:
            self.joystick.poll(self.episode, self.physics.tick)
            if self.joystick.connected:
                return
            self._wait_for_transport_event(self.joystick.generation)
        return 'quit'

    def _auto_wait_for_acceptance(self, accepted_before):
        generation = self.joystick.generation
        while not self.quit_requested:
            operation = self.joystick.poll(self.episode, self.physics.tick)
            if operation == 'reset':
                return 'reset'
            if not self.joystick.connected:
                self.quit_requested = True
                return 'quit'
            if self.joystick.generation != generation:
                self.quit_requested = True
                return 'quit'
            if self.joystick.accepted > accepted_before:
                return None
            self._wait_for_transport_event(generation)
        return 'quit'

    def _auto_wait_for_reset(self):
        generation = self.joystick.generation
        while not self.quit_requested:
            operation = self.joystick.poll(self.episode, self.physics.tick)
            if operation == 'reset':
                return 'reset'
            if not self.joystick.connected:
                self.quit_requested = True
                return 'quit'
            if self.joystick.generation != generation:
                self.quit_requested = True
                return 'quit'
            self._wait_for_transport_event(generation)
        return 'quit'

    def _auto_pace(self, started, simulated_tick, speed):
        target = simulated_tick / (self.config.hz * speed)
        delay = started + target - time.perf_counter()
        if delay > 0:
            time.sleep(delay)

    def _wait_for_transport_event(self, generation):
        started = time.perf_counter()
        waiter = getattr(self.transport, 'wait_for_event', None)
        if waiter is None:
            time.sleep(0)
        else:
            waiter(generation)
        if hasattr(self, '_auto_metrics'):
            self._auto_metrics['transport_wait_ms'] += (time.perf_counter() - started) * 1000

    def _check_open(self):
        if self.closed:
            raise RuntimeError('Game container is closed')

    def close(self):
        if self.closed:
            return
        self.closed = True
        for component in (self.joystick, self.monitor, self.window, self.transport, self.renderer):
            if component is not None:
                component.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
