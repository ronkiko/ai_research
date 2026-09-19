"""Operator control for Screen Server status and manual source binding."""
from __future__ import annotations

import argparse
import socket

from game2.v2.contracts.framing import recv_frame, send_frame
from game2.v2.contracts.screen import CURRENT_SCREEN_SOURCE_PATH, ScreenSourceDiscovery
from game2.v2.contracts.screen_server import (
    CURRENT_SCREEN_SERVER_PATH,
    ScreenServerDiscovery,
    bind_message,
    close_message,
    decode_screen_server_status,
    probe_message,
    unbind_message,
)


def request(discovery: ScreenServerDiscovery, message: dict):
    sock = socket.create_connection(
        (discovery.endpoint.host, discovery.endpoint.port), timeout=3
    )
    try:
        sock.settimeout(3)
        send_frame(sock, message)
        response = recv_frame(sock)
        if response.get("type") == "screen_server_error":
            raise RuntimeError(response.get("message", "Screen Server request failed"))
        return decode_screen_server_status(response)
    finally:
        sock.close()


def _print_status(status) -> None:
    for item in status:
        detail = (
            f" {item['map']} {item['session_id']}"
            if item["state"] == "bound" else ""
        )
        print(f"Screen {item['screen']}: {item['state']}{detail}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Control Game2 Screen slots")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status")

    bind = sub.add_parser("bind")
    bind.add_argument("screen", type=int)
    bind.add_argument("--source-discovery", default=str(CURRENT_SCREEN_SOURCE_PATH))

    unbind = sub.add_parser("unbind")
    unbind.add_argument("screen", type=int)

    close = sub.add_parser("close")
    close.add_argument("screen", type=int)

    parser.add_argument(
        "--server-discovery", default=str(CURRENT_SCREEN_SERVER_PATH)
    )
    args = parser.parse_args(argv)
    server = ScreenServerDiscovery.from_file(args.server_discovery)

    if args.command in (None, "status"):
        status = request(server, probe_message())
    elif args.command == "bind":
        source = ScreenSourceDiscovery.from_file(args.source_discovery)
        status = request(server, bind_message(args.screen, source))
    elif args.command == "unbind":
        status = request(server, unbind_message(args.screen))
    else:
        status = request(server, close_message(args.screen))
    _print_status(status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
