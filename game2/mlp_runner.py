"""Connect a local 2-4-2 MLP to the game2 TCP controller."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

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

    def run(self) -> None:
        frame = self.client.receive()
        completed = 0
        while completed < self.episodes:
            episode = frame['episode']
            started_tick = frame['tick']
            jumped = False
            log_probabilities = []
            entropies = []
            while frame['episode'] == episode and frame['status'] == 0:
                if frame['tick'] - started_tick >= self.max_ticks:
                    reward = -1.0
                    break
                reading = self.sensors.read(frame)
                jump_allowed = not jumped and reading.grounded and reading.features[0] <= self.jump_window
                if self.training:
                    decision = self.policy.sample(reading.features, jump_allowed=jump_allowed)
                    log_probabilities.append(decision.log_probability)
                    entropies.append(decision.entropy)
                else:
                    decision = self.policy.greedy(reading.features, jump_allowed=jump_allowed)
                right, jump = decision.right, decision.jump
                jumped = jumped or jump
                self.client.action(episode=episode, target_tick=frame['tick'] + self.target_delay,
                                   hold_ticks=self.hold_ticks, right=right, jump=jump)
                frame = self.client.receive()
            else:
                reward = 1.0 if frame['status'] == 2 else -1.0

            loss = self.policy.update(log_probabilities, entropies, reward) if self.training else 0.0
            completed += 1
            if self.training and (completed % self.save_every == 0 or completed == self.episodes):
                self.policy.save(self.checkpoint)
            print(json.dumps({
                'episode': episode,
                'result': 'success' if reward > 0 else 'die_or_timeout',
                'reward': reward,
                'loss': round(loss, 6),
                'tick': frame['tick'],
                'jumped': jumped,
                **self.policy.stats(),
            }), flush=True)
            if completed == self.episodes:
                return
            self.client.reset(episode)
            frame = self.client.receive()
            while frame['episode'] == episode:
                frame = self.client.receive()


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
