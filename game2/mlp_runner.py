"""Connect a local 3-8-2 MLP to the game2 TCP controller."""
from __future__ import annotations

from diagnostics import console_message

import argparse
from collections import deque
import json
import signal
import sys
import threading
import time
from pathlib import Path

import torch

from protocol import MAX_FUTURE, MAX_HOLD

from mlp_client import MLPClient
from mlp_382 import MLP382Policy
from sensors import ObservationSensors


DEFAULT_CHECKPOINT = Path(__file__).with_name('models') / '3-8-2' / 'weights.pt'


class RollingEpisodeStats:
    WINDOW = 100

    def __init__(self):
        self._window = deque(maxlen=self.WINDOW)
        self.attempts = 0
        self.successes = 0

    def record(self, success: bool, terminal_tick: int) -> None:
        self._window.append((bool(success), int(terminal_tick)))
        self.attempts += 1
        self.successes += int(success)

    def summary(self) -> dict:
        episodes_window = len(self._window)
        successes_window = sum(success for success, _ in self._window)
        terminal_ticks = [tick for _, tick in self._window]
        return {
            'success_rate_100': successes_window / episodes_window,
            'successes_100': successes_window,
            'episodes_window': episodes_window,
            'mean_terminal_tick_100': sum(terminal_ticks) / episodes_window,
            'attempts': self.attempts,
            'successes': self.successes,
            'success_rate_total': self.successes / self.attempts,
        }


def connect(host: str, port: int, timeout: int, wait: float, stop_event=None) -> MLPClient:
    deadline = time.monotonic() + wait
    while True:
        if stop_event is not None and stop_event.is_set():
            raise ConnectionError('MLP connection cancelled')
        try:
            return MLPClient(host=host, port=port, timeout=timeout)
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))


class MlpRunner:
    def __init__(self, client: MLPClient, policy: MLP382Policy, *, training: bool,
                 episodes: int, checkpoint: Path, max_ticks: int, target_delay: int,
                 hold_ticks: int, save_every: int, event_sink=None, stop_event=None,
                 console_output: bool = True):
        self.console_output = console_output
        self.client = client
        self.policy = policy
        self.training = training
        self.episodes = episodes
        self.checkpoint = checkpoint
        self.max_ticks = max_ticks
        self.target_delay = target_delay
        self.hold_ticks = hold_ticks
        self.save_every = save_every
        self.event_sink = event_sink
        self.stop_event = stop_event
        self.sensors = ObservationSensors()
        self.episode_stats = RollingEpisodeStats()

    def _emit(self, event, **payload):
        if self.event_sink is not None:
            self.event_sink(dict(type=event, **payload))

    def _stopping(self):
        return self.stop_event is not None and self.stop_event.is_set()

    def _reset(self, frame):
        episode = frame['episode']
        self.client.reset(episode)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            frame = self.client.receive()
            if frame['episode'] != episode:
                return frame
        raise TimeoutError('Game did not acknowledge reset')

    def run(self) -> None:
        # Never credit a previous controller's terminal result or partial attempt.
        frame = self._reset(self.client.receive())
        completed = 0
        while completed < self.episodes:
            if self._stopping():
                return
            episode = frame['episode']
            started_tick = frame['tick']
            transport_start = (frame['late'], frame['rejected'])
            jump_start = (frame.get('jump_requested', 0), frame.get('jump_applied', 0))
            pending = []
            last_tick = -1
            last_frame = frame
            while frame['episode'] == episode and frame['status'] == 0:
                if self._stopping():
                    return
                if frame['tick'] - started_tick >= self.max_ticks:
                    break
                if frame['tick'] == last_tick:
                    frame = self.client.receive()
                    continue
                last_tick = frame['tick']
                reading = self.sensors.read(frame)
                if self.training:
                    decision = self.policy.sample(reading.features)
                else:
                    decision = self.policy.greedy(reading.features)
                target = frame['tick'] + self.target_delay
                sequence = self.client.action(episode=episode, target_tick=target,
                                              hold_ticks=self.hold_ticks,
                                              right=decision.right, jump=decision.jump)
                pending.append((sequence, target, decision))
                last_frame = frame
                self._emit('live_stats', episode=episode, tick=frame['tick'],
                           result='running', late=frame['late'] - transport_start[0],
                           rejected=frame['rejected'] - transport_start[1])
                frame = self.client.receive()

            same_episode = frame['episode'] == episode
            clean_transport = transport_start == (frame['late'], frame['rejected'])
            # ACK is cumulative. With no rejection and unique target ticks it proves
            # acceptance of earlier sequence numbers. Future commands did not act.
            executed = [decision for seq, target, decision in pending
                        if same_episode and seq <= frame['accepted'] and target <= frame['tick']]
            reward = 1.0 if same_episode and frame['status'] == 2 else -1.0
            trainable = same_episode and clean_transport and bool(executed)
            terminal_frame = frame if same_episode else last_frame
            jump_requested = max(0, terminal_frame.get('jump_requested', 0) - jump_start[0])
            jump_applied = max(0, terminal_frame.get('jump_applied', 0) - jump_start[1])
            terminal_tick = terminal_frame['tick']
            self.episode_stats.record(reward > 0, terminal_tick)
            loss = 0.0
            if self.training and trainable:
                loss = self.policy.update([d.log_probability for d in executed],
                                          [d.entropy for d in executed], reward)
            completed += 1
            if self.training and (completed % self.save_every == 0 or completed == self.episodes):
                self.policy.save(self.checkpoint)
            result = ('success' if reward > 0 else
                      'die' if same_episode and frame['status'] == 1 else 'timeout_or_reset')
            metrics = {
                'episode': episode, 'result': result, 'reward': reward,
                'loss': round(loss, 6), 'tick': terminal_tick,
                'jump_requested': jump_requested,
                'jump_applied': jump_applied,
                'executed_actions': len(executed),
                'updated': self.training and trainable,
                'late': frame['late'] - transport_start[0],
                'rejected': frame['rejected'] - transport_start[1],
                **self.episode_stats.summary(),
                **self.policy.stats(),
            }
            self._emit('episode_finished', **metrics)
            if self.console_output:
                console_message(json.dumps(metrics), flush=True)
            if completed == self.episodes:
                return
            frame = self._reset(frame)

    def save_checkpoint(self):
        """Persist the current policy after a controlled GUI shutdown."""
        if self.training:
            self.policy.save(self.checkpoint)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description='game2 3-8-2 MLP')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--mode', choices=('train', 'play'), default='train')
    parser.add_argument('--episodes', type=int, default=1000)
    parser.add_argument('--checkpoint', type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--fresh', action='store_true',
                        help='ignore an existing checkpoint in train mode')
    parser.add_argument('--wait', type=float, default=20.0,
                        help='seconds to wait for the game socket')
    parser.add_argument('--max-ticks', type=int, default=600)
    parser.add_argument('--target-delay', type=int, default=32,
                        help='future physics ticks used to absorb inference latency')
    parser.add_argument('--hold-ticks', type=int, default=48)
    parser.add_argument('--save-every', type=int, default=10)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.episodes < 1 or args.save_every < 1:
        raise SystemExit('--episodes and --save-every must be positive')
    if not 1 <= args.target_delay <= MAX_FUTURE or not 1 <= args.hold_ticks <= MAX_HOLD:
        raise SystemExit('--target-delay and --hold-ticks must be in [1, 120]')
    if args.max_ticks <= args.target_delay or args.wait < 0:
        raise SystemExit('Invalid max-ticks or wait')
    # A 50-parameter network gains nothing from a large CPU thread pool.
    torch.set_num_threads(1)
    stop_event = threading.Event()

    def request_stop(_signum, _frame):
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    policy = MLP382Policy(seed=args.seed)
    if args.mode == 'play' or (args.mode == 'train' and not args.fresh
                               and args.checkpoint.exists()):
        if not args.checkpoint.exists():
            raise SystemExit(f'checkpoint not found: {args.checkpoint}')
        policy.load(args.checkpoint)
    try:
        with connect(args.host, args.port, timeout=2, wait=args.wait) as client:
            runner = MlpRunner(client, policy, training=args.mode == 'train',
                               episodes=args.episodes, checkpoint=args.checkpoint,
                               max_ticks=args.max_ticks, target_delay=args.target_delay,
                               hold_ticks=args.hold_ticks,
                               save_every=args.save_every, stop_event=stop_event)
            try:
                runner.run()
            finally:
                if runner.training:
                    runner.save_checkpoint()
    except (ConnectionError, OSError, TimeoutError, ValueError) as error:
        print(f'game2 MLP: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
