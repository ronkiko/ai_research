"""Connect a local 2-4-2 MLP to the game2 TCP controller."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import torch

from protocol import MAX_FUTURE, MAX_HOLD

from mlp_client import MLPClient
from mlp_242 import MLP242Policy
from sensors import PixelSensors


DEFAULT_CHECKPOINT = Path(__file__).with_name('models') / '2-4-2' / 'weights.pt'


def connect(host: str, port: int, timeout: int, wait: float) -> MLPClient:
    deadline = time.monotonic() + wait
    while True:
        try:
            return MLPClient(host=host, port=port, timeout=timeout)
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.25)


class MlpRunner:
    def __init__(self, client: MLPClient, policy: MLP242Policy, *, training: bool,
                 episodes: int, checkpoint: Path, max_ticks: int, target_delay: int,
                 hold_ticks: int, jump_window: float, save_every: int):
        self.client = client
        self.policy = policy
        self.training = training
        self.episodes = episodes
        self.checkpoint = checkpoint
        self.max_ticks = max_ticks
        self.target_delay = target_delay
        self.hold_ticks = hold_ticks
        self.jump_window = jump_window
        self.save_every = save_every
        self.sensors = PixelSensors()

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
            episode = frame['episode']
            started_tick = frame['tick']
            transport_start = (frame['late'], frame['rejected'])
            jumped = False
            pending = []
            last_tick = -1
            while frame['episode'] == episode and frame['status'] == 0:
                if frame['tick'] - started_tick >= self.max_ticks:
                    break
                if frame['tick'] == last_tick:
                    frame = self.client.receive()
                    continue
                last_tick = frame['tick']
                reading = self.sensors.read(frame)
                jump_allowed = not jumped and reading.grounded and reading.features[0] <= self.jump_window
                if self.training:
                    decision = self.policy.sample(reading.features, jump_allowed=jump_allowed)
                else:
                    decision = self.policy.greedy(reading.features, jump_allowed=jump_allowed)
                target = frame['tick'] + self.target_delay
                sequence = self.client.action(episode=episode, target_tick=target,
                                              hold_ticks=self.hold_ticks,
                                              right=decision.right, jump=decision.jump)
                pending.append((sequence, target, decision))
                jumped = jumped or decision.jump
                frame = self.client.receive()

            same_episode = frame['episode'] == episode
            clean_transport = transport_start == (frame['late'], frame['rejected'])
            # ACK is cumulative. With no rejection and unique target ticks it proves
            # acceptance of earlier sequence numbers. Future commands did not act.
            executed = [decision for seq, target, decision in pending
                        if same_episode and seq <= frame['accepted'] and target <= frame['tick']]
            reward = 1.0 if same_episode and frame['status'] == 2 else -1.0
            trainable = same_episode and clean_transport and bool(executed)
            loss = 0.0
            if self.training and trainable:
                loss = self.policy.update([d.log_probability for d in executed],
                                          [d.entropy for d in executed], reward)
            completed += 1
            if self.training and (completed % self.save_every == 0 or completed == self.episodes):
                self.policy.save(self.checkpoint)
            result = ('success' if reward > 0 else
                      'die' if same_episode and frame['status'] == 1 else 'timeout_or_reset')
            print(json.dumps({
                'episode': episode, 'result': result, 'reward': reward,
                'loss': round(loss, 6), 'tick': frame['tick'],
                'jumped': any(d.jump for d in executed),
                'executed_actions': len(executed),
                'updated': self.training and trainable,
                'late': frame['late'] - transport_start[0],
                'rejected': frame['rejected'] - transport_start[1],
                **self.policy.stats(),
            }), flush=True)
            if completed == self.episodes:
                return
            frame = self._reset(frame)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description='game2 2-4-2 MLP')
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
    parser.add_argument('--jump-window', type=float, default=0.08,
                        help='maximum normalized distance at which jumping is considered')
    parser.add_argument('--save-every', type=int, default=10)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.episodes < 1 or args.save_every < 1:
        raise SystemExit('--episodes and --save-every must be positive')
    if not 1 <= args.target_delay <= MAX_FUTURE or not 1 <= args.hold_ticks <= MAX_HOLD:
        raise SystemExit('--target-delay and --hold-ticks must be in [1, 120]')
    if args.max_ticks <= args.target_delay or not 0 <= args.jump_window <= 1 or args.wait < 0:
        raise SystemExit('Invalid max-ticks, jump-window or wait')
    # A 22-parameter network gains nothing from a large CPU thread pool.
    torch.set_num_threads(1)
    policy = MLP242Policy(seed=args.seed)
    if args.mode == 'play' or (args.mode == 'train' and not args.fresh
                               and args.checkpoint.exists()):
        if not args.checkpoint.exists():
            raise SystemExit(f'checkpoint not found: {args.checkpoint}')
        policy.load(args.checkpoint)
    try:
        with connect(args.host, args.port, timeout=2, wait=args.wait) as client:
            MlpRunner(client, policy, training=args.mode == 'train',
                      episodes=args.episodes, checkpoint=args.checkpoint,
                      max_ticks=args.max_ticks, target_delay=args.target_delay,
                      hold_ticks=args.hold_ticks, jump_window=args.jump_window,
                      save_every=args.save_every).run()
    except (ConnectionError, OSError, TimeoutError, ValueError) as error:
        print(f'game2 MLP: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
