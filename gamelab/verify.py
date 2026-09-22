"""Frozen learned-policy verification."""
from __future__ import annotations

import argparse
import json

from .config import DEFAULT_GOAL_TIMEOUT, SUCCESS_TOLERANCE
from .host import HostClient
from .runtime import GoalRunner, ensure_player, load_runtime_model, reset_player_state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify frozen GameLab policy")
    parser.add_argument("--target", type=float, default=987.0)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--player", default="player1")
    parser.add_argument("--tolerance", type=float, default=SUCCESS_TOLERANCE)
    parser.add_argument("--timeout", type=float, default=DEFAULT_GOAL_TIMEOUT)
    args = parser.parse_args(argv)

    if args.runs <= 0:
        raise SystemExit("--runs must be positive")

    model = load_runtime_model()
    client = HostClient("gamelab-verify")
    passed = 0
    try:
        ensure_player(client, args.player)
        for index in range(1, args.runs + 1):
            reset_player_state(client, args.player)
            result = GoalRunner(model, client, player_id=args.player).run(
                args.target,
                tolerance=args.tolerance,
                max_seconds=args.timeout,
            )
            ok = result.get("status") == "reached"
            passed += int(ok)
            print(
                json.dumps(
                    {"verify": index, "pass": ok, **result},
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                flush=True,
            )
        print(f"VERIFY {passed}/{args.runs} target={args.target:g}", flush=True)
        return 0 if passed == args.runs else 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
