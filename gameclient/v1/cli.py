"""Plain, line-oriented CLI for GameServer v1.

No curses, no TUI, no ANSI redraws. Every command terminates unless `watch` is
explicitly requested. `--json` emits stable machine-readable JSON/JSONL.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .client import DIRECTIONS, GameClient, GatewayError
from .config import DEFAULT_HOST, DEFAULT_PORT, DEFAULT_SESSION_FILE, DEFAULT_TIMEOUT
from .state import SessionError


class CliError(RuntimeError):
    pass


def _json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))


def _entity_line(entity: dict[str, Any]) -> str:
    return (
        f"ENTITY id={entity.get('entity_id')} kind={entity.get('kind')} "
        f"owner={entity.get('owner_id')} x={float(entity.get('x', 0.0)):.3f} "
        f"vx={float(entity.get('vx', 0.0)):.3f} move={entity.get('move_x')}"
    )


def _print_snapshot(snapshot: dict[str, Any]) -> None:
    entities = snapshot.get("entities")
    if not isinstance(entities, list):
        entities = []
    print(
        f"SNAPSHOT zone={snapshot.get('zone_id')} tick={snapshot.get('world_tick')} "
        f"physics_hz={snapshot.get('physics_hz')} line_length={snapshot.get('line_length')} "
        f"entities={len(entities)}"
    )
    for entity in entities:
        if isinstance(entity, dict):
            print(_entity_line(entity))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gameclient.v1",
        description="Plain CLI client for the GameServer v1 public Gateway.",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Gateway host (default: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Gateway port (default: {DEFAULT_PORT})")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="Gateway RPC timeout in seconds")
    parser.add_argument(
        "--session-file",
        type=Path,
        default=DEFAULT_SESSION_FILE,
        help="local session state file",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON; watch emits JSONL")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("health", help="check public Gateway availability")
    sub.add_parser("players", help="list demo player IDs available in the passwordless lobby")

    login = sub.add_parser("login", help="select one player ID and create a local session")
    login.add_argument("player_id")

    sub.add_parser("whoami", help="show the locally stored session without contacting the server")

    sub.add_parser("snapshot", help="read the latest authoritative zone snapshot")

    exact = sub.add_parser("input", help="send exact one-dimensional movement; intended for agents and scripts")
    exact.add_argument("--x", dest="move_x", type=int, required=True, choices=(-1, 0, 1))

    move = sub.add_parser("move", help="human-friendly movement alias")
    move.add_argument("direction", choices=tuple(name for name in DIRECTIONS if name != "stop"))

    sub.add_parser("stop", help="set movement intent to zero")
    sub.add_parser("logout", help="logout from Gateway and remove the local session")
    sub.add_parser(
        "forget-session",
        help="remove only local session state after a server restart; does not contact Gateway",
    )

    watch = sub.add_parser("watch", help="print repeated authoritative snapshots; plain lines, no TUI")
    watch.add_argument("--interval", type=float, default=0.5, help="seconds between requests (default: 0.5)")
    watch.add_argument("--count", type=int, default=0, help="number of snapshots; 0 means until Ctrl+C")
    return parser


def _client(args: argparse.Namespace) -> GameClient:
    if not 0 < args.port <= 65535:
        raise CliError("port must be between 1 and 65535")
    if args.timeout <= 0:
        raise CliError("timeout must be > 0")
    return GameClient(
        host=args.host,
        port=args.port,
        timeout=args.timeout,
        session_file=args.session_file,
    )


def run(args: argparse.Namespace) -> int:
    client = _client(args)
    command = args.command

    if command == "health":
        response = client.health()
        if args.json:
            _json({"ok": True, "command": command, "response": response})
        else:
            print(f"GATEWAY status={response.get('status')} component={response.get('component')}")
        return 0

    if command == "players":
        players = client.players()
        if args.json:
            _json({"ok": True, "command": command, "players": players})
        else:
            print(f"PLAYERS count={len(players)}")
            for player_id in players:
                print(f"PLAYER id={player_id}")
        return 0

    if command == "login":
        session = client.login(args.player_id)
        if args.json:
            _json({"ok": True, "command": command, "session": session})
        else:
            print(
                f"LOGIN player={session['player_id']} entity={session['entity_id']} "
                f"world={session['world_id']} zone={session['zone_id']} session={session['session_id']}"
            )
        return 0

    if command == "whoami":
        session = client.whoami()
        if args.json:
            _json({"ok": True, "command": command, "session": session})
        else:
            print(
                f"SESSION player={session['player_id']} entity={session['entity_id']} "
                f"world={session['world_id']} zone={session['zone_id']} "
                f"sequence={session['sequence']} session={session['session_id']}"
            )
        return 0

    if command == "snapshot":
        snapshot = client.snapshot()
        if args.json:
            _json({"ok": True, "command": command, "snapshot": snapshot})
        else:
            _print_snapshot(snapshot)
        return 0

    if command in {"input", "move", "stop"}:
        if command == "input":
            result = client.input(args.move_x)
        elif command == "move":
            result = client.move(args.direction)
        else:
            result = client.move("stop")
        if args.json:
            _json({"ok": True, "command": command, **result})
        else:
            response = result["response"]
            print(
                f"INPUT sequence={result['sequence']} move={result['move_x']} "
                f"status={response.get('type')} queued_at_tick={response.get('world_tick')} "
                f"command_id={response.get('command_id')}"
            )
        return 0

    if command == "logout":
        response = client.logout()
        if args.json:
            _json({"ok": True, "command": command, "response": response})
        else:
            print(
                f"LOGOUT player={response.get('player_id')} entity={response.get('entity_id')} "
                f"zone={response.get('zone_id')}"
            )
        return 0

    if command == "forget-session":
        existed = client.forget_local_session()
        if args.json:
            _json({"ok": True, "command": command, "forgot": existed})
        else:
            print("LOCAL_SESSION forgotten" if existed else "LOCAL_SESSION absent")
        return 0

    if command == "watch":
        index = 0
        try:
            for snapshot in client.watch(interval=args.interval, count=args.count):
                index += 1
                if args.json:
                    _json({"ok": True, "command": command, "index": index, "snapshot": snapshot})
                else:
                    print(f"WATCH index={index}")
                    _print_snapshot(snapshot)
                    sys.stdout.flush()
        except KeyboardInterrupt:
            if not args.json:
                print("WATCH stopped")
        return 0

    raise CliError(f"unknown command: {command}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (CliError, GatewayError, SessionError, ValueError) as exc:
        if getattr(args, "json", False):
            _json({"ok": False, "command": getattr(args, "command", None), "error": str(exc)})
        else:
            print(f"ERROR {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
