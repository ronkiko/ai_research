"""Plain CLI Client for GameClient Host."""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from .base import HostClient, HostClientError


DIRECTIONS = {"left": -1, "stop": 0, "right": 1}


def _json(payload: Any) -> None:
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))


def _print_state(response: dict[str, Any]) -> None:
    snapshot = response["snapshot"]
    entities = snapshot.get("entities", [])
    print(
        f"STATE zone={snapshot.get('zone_id')} tick={snapshot.get('world_tick')} "
        f"line_length={snapshot.get('line_length')} entities={len(entities)}"
    )
    for entity in entities:
        marker = "P" if entity.get("kind") == "player" else "B" if entity.get("entity_id") == "mob1" else "?"
        print(
            f"ENTITY marker={marker} id={entity.get('entity_id')} "
            f"x={float(entity.get('x', 0.0)):.3f} "
            f"vx={float(entity.get('vx', 0.0)):.3f} motor={float(entity.get('motor_x', 0.0)):+.3f}"
        )
    event = response.get("last_event")
    if event:
        print(
            f"LAST_EVENT id={event.get('event_id')} kind={event.get('kind')} "
            f"client={event.get('client_id')}"
        )


def _print_events(response: dict[str, Any]) -> None:
    print(
        f"EVENTS after={response.get('after_event_id')} "
        f"latest={response.get('latest_event_id')} count={len(response.get('events', []))}"
    )
    for event in response.get("events", []):
        fields = [
            f"id={event.get('event_id')}",
            f"kind={event.get('kind')}",
            f"client={event.get('client_id')}",
        ]
        if "sequence" in event:
            fields.append(f"sequence={event.get('sequence')}")
        if "motor_x" in event:
            fields.append(f"motor={float(event.get('motor_x', 0.0)):+.3f}")
        if "move_x" in event:
            fields.append(f"manual_move={event.get('move_x')}")
        print("EVENT " + " ".join(fields))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gameclient.v1.cli-client",
        description="Human/automation CLI Client attached to GameClient Host.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=17700)
    parser.add_argument("--timeout", type=float, default=1.0)
    parser.add_argument("--client-id", default="cli")
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("health", "describe", "players", "session", "state", "logout"):
        sub.add_parser(name)
    login = sub.add_parser("login")
    login.add_argument("player_id", nargs="?", default="player1")
    move = sub.add_parser("move")
    move.add_argument("direction", choices=tuple(DIRECTIONS))
    exact = sub.add_parser("input")
    exact.add_argument("--x", type=int, required=True, choices=(-1, 0, 1))
    events = sub.add_parser("events")
    events.add_argument("--after", type=int, default=0)
    events.add_argument("--limit", type=int, default=50)
    watch = sub.add_parser("watch")
    watch.add_argument("--interval", type=float, default=0.5)
    watch.add_argument("--count", type=int, default=0, help="0 means forever")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    client = HostClient(
        args.client_id,
        host=args.host,
        port=args.port,
        timeout=args.timeout,
    )
    try:
        command = args.command
        if command == "health":
            result = client.health()
        elif command == "describe":
            result = client.describe()
        elif command == "players":
            result = {"players": client.players()}
        elif command == "login":
            result = client.login(args.player_id)
        elif command == "session":
            result = client.session()
        elif command == "state":
            result = client.state()
        elif command == "move":
            result = client.input(DIRECTIONS[args.direction])
        elif command == "input":
            result = client.input(args.x)
        elif command == "events":
            result = client.events(args.after, limit=args.limit)
        elif command == "logout":
            result = client.logout()
        elif command == "watch":
            seen = 0
            event_id = 0
            while args.count == 0 or seen < args.count:
                result = client.state()
                if args.json:
                    _json(result)
                else:
                    _print_state(result)
                    events = client.events(event_id)
                    if events["events"]:
                        _print_events(events)
                    event_id = events.get("next_after_event_id", event_id)
                seen += 1
                if args.count == 0 or seen < args.count:
                    time.sleep(args.interval)
            return 0
        else:
            raise RuntimeError(f"unknown command: {command}")

        if args.json:
            _json(result)
        elif command == "state":
            _print_state(result)
        elif command == "events":
            _print_events(result)
        else:
            _json(result)
        return 0
    except (HostClientError, ValueError, KeyError) as exc:
        if args.json:
            _json({"ok": False, "error": str(exc)})
        else:
            print(f"ERROR {exc}", file=sys.stderr)
        return 2
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
