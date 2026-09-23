"""Mob Server: blind one-dimensional random-walk intent producer."""
from __future__ import annotations

import argparse
import random
import time

from ..common.config import HOST, ZONE_PORT
from ..common.protocol import message, rpc


class MobService:
    """Generate mob intent without observing player/world coordinates.

    Until a real sensor contract exists, the v1 mob is intentionally blind.
    It performs a random walk by periodically choosing -1, 0, or +1.
    """

    def __init__(
        self,
        *,
        host: str = HOST,
        zone_port: int = ZONE_PORT,
        wander_hz: float = 1.0,
        rng: random.Random | None = None,
    ):
        if wander_hz <= 0:
            raise ValueError("wander_hz must be > 0")
        self.host = host
        self.zone_port = zone_port
        self.wander_hz = wander_hz
        self.rng = rng or random.Random()
        self.sequence = 0

    def next_intent(self) -> int:
        return self.rng.choice((-1, 0, 1))

    def run(self) -> None:
        print(
            '{"component":"mob","status":"READY","mob_id":"mob1","mode":"blind_random_walk"}',
            flush=True,
        )
        period = 1.0 / self.wander_hz
        next_decision = time.monotonic() + period
        while True:
            delay = next_decision - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_decision = time.monotonic()

            motor_x = self.next_intent()
            self.sequence += 1
            try:
                rpc(
                    self.host,
                    self.zone_port,
                    message(
                        "input",
                        entity_id="mob1",
                        sequence=self.sequence,
                        motor_x=motor_x,
                        source="mob",
                    ),
                    timeout=0.5,
                )
            except OSError:
                pass

            next_decision += period


def main() -> int:
    parser = argparse.ArgumentParser(description="GameServer v1 Mob Server")
    parser.add_argument("--wander-hz", type=float, default=1.0)
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="optional deterministic random seed for laboratory reproduction",
    )
    args = parser.parse_args()
    rng = random.Random(args.seed) if args.seed is not None else random.Random()
    MobService(wander_hz=args.wander_hz, rng=rng).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
