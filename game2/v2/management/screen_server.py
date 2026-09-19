"""Independent operator Screen Server.

This cut intentionally owns only numbered screen slots and its own lifecycle.
It does not import Console, Player, or Training runtimes and does not bind a
screen to a gameplay source yet.
"""
from __future__ import annotations

import argparse
import json
import signal
import socket
import threading
from pathlib import Path

from game2.v2.contracts.framing import recv_frame, send_frame
from game2.v2.contracts.manifests import Endpoint
from game2.v2.contracts.screen_server import (
    CURRENT_SCREEN_SERVER_PATH,
    ScreenServerDiscovery,
    decode_screen_server_request,
    publish_screen_server,
    remove_screen_server,
    status_message,
)


class ScreenServer:
    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        slots: int = 4,
    ) -> None:
        if type(host) is not str or not host:
            raise ValueError("host must be non-empty")
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("port must be in 0..65535")
        if type(slots) is not int or slots <= 0:
            raise ValueError("slots must be a positive integer")
        self.host = host
        self.port = port
        self.slots = slots
        self.listener: socket.socket | None = None
        self.endpoint: Endpoint | None = None
        self.stop_requested = threading.Event()
        self.ready = threading.Event()
        self.discovery: ScreenServerDiscovery | None = None
        self.discovery_path: Path | None = None

    def request_stop(self) -> None:
        self.stop_requested.set()

    def _handle_client(self, client: socket.socket) -> None:
        client.settimeout(1.0)
        try:
            while not self.stop_requested.is_set():
                try:
                    request = recv_frame(client)
                except socket.timeout:
                    continue
                decode_screen_server_request(request)
                send_frame(client, status_message(self.slots))
        except (EOFError, OSError, ValueError):
            return
        finally:
            try:
                client.close()
            except OSError:
                pass

    def start(
        self,
        discovery_path: str | Path = CURRENT_SCREEN_SERVER_PATH,
    ) -> ScreenServerDiscovery:
        if self.listener is not None:
            raise RuntimeError("Screen Server is already started")
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.host, self.port))
        listener.listen()
        listener.settimeout(0.25)
        bound_host, bound_port = listener.getsockname()[:2]
        self.listener = listener
        self.endpoint = Endpoint(str(bound_host), int(bound_port))
        self.discovery = ScreenServerDiscovery(1, self.endpoint, self.slots)
        self.discovery_path = Path(discovery_path)
        publish_screen_server(self.discovery, self.discovery_path)
        self.ready.set()
        return self.discovery

    def run(
        self,
        discovery_path: str | Path = CURRENT_SCREEN_SERVER_PATH,
    ) -> int:
        discovery = self.start(discovery_path)
        print("READY " + json.dumps(discovery.to_dict(), sort_keys=True), flush=True)
        workers: list[threading.Thread] = []
        try:
            while not self.stop_requested.is_set():
                listener = self.listener
                if listener is None:
                    break
                try:
                    client, _address = listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if self.stop_requested.is_set():
                        break
                    raise
                worker = threading.Thread(
                    target=self._handle_client,
                    args=(client,),
                    name="game2-v2-screen-server-client",
                    daemon=True,
                )
                worker.start()
                workers.append(worker)
        finally:
            self.close()
            for worker in workers:
                worker.join(timeout=1)
        return 0

    def close(self) -> None:
        self.stop_requested.set()
        listener = self.listener
        self.listener = None
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        if self.discovery is not None and self.discovery_path is not None:
            remove_screen_server(self.discovery, self.discovery_path)
        self.ready.set()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Game2 V2 independent Screen Server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--slots", type=int, default=4)
    parser.add_argument(
        "--discovery",
        default=str(CURRENT_SCREEN_SERVER_PATH),
    )
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    server = ScreenServer(host=args.host, port=args.port, slots=args.slots)
    previous_int = signal.signal(
        signal.SIGINT, lambda _signum, _frame: server.request_stop()
    )
    previous_term = signal.signal(
        signal.SIGTERM, lambda _signum, _frame: server.request_stop()
    )
    try:
        return server.run(args.discovery)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"ERROR {type(exc).__name__}: {exc}", flush=True)
        return 1
    finally:
        server.close()
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)


if __name__ == "__main__":
    raise SystemExit(main())
