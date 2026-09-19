"""Operator client for binding a numbered Screen to a Screen source."""
from __future__ import annotations
import argparse, socket
from game2.v2.contracts.framing import recv_frame, send_frame
from game2.v2.contracts.screen import CURRENT_SCREEN_SOURCE_PATH, ScreenSourceDiscovery
from game2.v2.contracts.screen_server import (
    CURRENT_SCREEN_SERVER_PATH, ScreenServerDiscovery, bind_message,
    decode_screen_server_status, probe_message, unbind_message,
)

def request(discovery, message):
    sock=socket.create_connection((discovery.endpoint.host,discovery.endpoint.port),timeout=2)
    try:
        sock.settimeout(2); send_frame(sock,message)
        return decode_screen_server_status(recv_frame(sock))
    finally: sock.close()

def main(argv=None):
    p=argparse.ArgumentParser(description="Bind Game2 Screen slots")
    p.add_argument("screen", nargs="?", type=int)
    p.add_argument("action", nargs="?", choices=("on","off"), default="on")
    p.add_argument("--server-discovery",default=str(CURRENT_SCREEN_SERVER_PATH))
    p.add_argument("--source-discovery",default=str(CURRENT_SCREEN_SOURCE_PATH))
    a=p.parse_args(argv)
    server=ScreenServerDiscovery.from_file(a.server_discovery)
    if a.screen is None:
        status=request(server,probe_message())
    elif a.action=="off":
        status=request(server,unbind_message(a.screen))
    else:
        source=ScreenSourceDiscovery.from_file(a.source_discovery)
        status=request(server,bind_message(a.screen,source))
    for item in status:
        detail = f" {item['map']} {item['session_id']}" if item["state"]=="bound" else ""
        print(f"Screen {item['screen']}: {item['state']}{detail}")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
