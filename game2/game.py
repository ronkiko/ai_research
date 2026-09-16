"""Composition root and lifecycle owner for game2 components."""
import math
import sys
import time

from controls import Action
from joysticks import HumanJoystick
from level import DEFAULT_MAP, load_level
from monitors import AgentMonitor, ColorRenderer, WindowMonitor
from physics import PhysicsConfig, PhysicsWorld
from socket_io import SocketTransport
from socket_joystick import SocketJoystick


class GameContainer:
    def __init__(self, map_path=DEFAULT_MAP, mode='human', *, port=8765, config=None,
                 monitor_hz=30, window=False):
        if mode not in ('human', 'agent'):
            raise ValueError('mode must be human or agent')
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError('port must be an integer in [0, 65535]')
        self.config = config or PhysicsConfig()
        if self.config.hz > 65535:
            raise ValueError('physics Hz exceeds protocol limit')
        if type(monitor_hz) is not int or monitor_hz < 1 or self.config.hz % monitor_hz:
            raise ValueError('monitor_hz must be a positive divisor of physics Hz')
        self.monitor_hz = monitor_hz
        self.mode = mode
        self.map_path = map_path
        self.level = load_level(map_path)
        self.physics = PhysicsWorld(self.level.new_body(), self.level.surfaces, self.config)
        self.renderer = ColorRenderer()
        self.transport = self.monitor = self.joystick = self.window = None
        self.episode = 1
        self.status = self.event_sequence = self.last_event = 0
        self._accumulator = self._discarded = 0.0
        self._frame_key = self._frame = None
        self.closed = self.quit_requested = False
        try:
            if mode == 'human':
                self.monitor = WindowMonitor(self.level.width, self.level.height)
                self.joystick = HumanJoystick()
            else:
                self.transport = SocketTransport(port=port)
                self.joystick = SocketJoystick(self.transport)
                self.monitor = AgentMonitor(self.transport)
                if window:
                    self.window = WindowMonitor(self.level.width, self.level.height, spectator=True)
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
        events = self.physics.step(move=int(action.right), jump=action.jump)
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
            if self.mode == 'agent':
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
        key = (self.episode, self.physics.tick)
        if key != self._frame_key:
            self._frame = self.renderer.render(self.level.width, self.level.height,
                                               self.level.surfaces, self.body)
            self._frame_key = key
        return self._frame

    def metadata(self):
        return dict(episode=self.episode, tick=self.physics.tick, hz=self.config.hz,
                    monitor_hz=self.monitor_hz, status=self.status,
                    accepted=getattr(self.joystick, 'accepted', 0),
                    late=getattr(self.joystick, 'late', 0),
                    rejected=getattr(self.joystick, 'rejected', 0),
                    overrun_ticks=int(self._discarded * self.config.hz),
                    event_sequence=self.event_sequence, last_event=self.last_event)

    def present(self):
        self._check_open()
        frame, metadata = self.frame(), self.metadata()
        self.monitor.present(frame, metadata)
        if self.window is not None:
            self.window.present(frame, metadata)

    def run(self):
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
