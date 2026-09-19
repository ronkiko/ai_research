"""Independent broker for externally launched Game2 Screen windows."""
from __future__ import annotations

import argparse
import json
import signal
import socket
import threading
from dataclasses import dataclass, field
from pathlib import Path

from game2.v2.contracts.framing import recv_frame, send_frame
from game2.v2.contracts.manifests import Endpoint
from game2.v2.contracts.screen import ScreenSourceDiscovery
from game2.v2.contracts.screen_server import (
    BIND, CLOSE, CURRENT_SCREEN_SERVER_PATH, OPEN, PROBE, UNBIND,
    ScreenServerDiscovery, attach_message, decode_screen_server_request,
    detach_message, opened_message, publish_screen_server, remove_screen_server,
    slot_close_message, status_message,
)


@dataclass
class ScreenRegistration:
    screen: int
    socket: socket.socket
    send_lock: threading.Lock = field(default_factory=threading.Lock)
    closed: bool = False

    def send(self, message: dict) -> None:
        with self.send_lock:
            if self.closed:
                raise ConnectionError("Screen window is disconnected")
            send_frame(self.socket, message)

    def close(self) -> None:
        self.closed = True
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.socket.close()
        except OSError:
            pass


class ScreenServer:
    """Broker only: it never launches pygame or owns graphical processes."""

    def __init__(self, *, host="127.0.0.1", port=0, slots=4):
        if type(slots) is not int or slots <= 0:
            raise ValueError("slots must be positive")
        self.host, self.port, self.slots = host, port, slots
        self.listener: socket.socket | None = None
        self.endpoint: Endpoint | None = None
        self.stop_requested = threading.Event()
        self.ready = threading.Event()
        self.discovery: ScreenServerDiscovery | None = None
        self.discovery_path: Path | None = None
        self.registrations: dict[int, ScreenRegistration] = {}
        self.sources: dict[int, ScreenSourceDiscovery] = {}
        self.lock = threading.RLock()

    def request_stop(self) -> None:
        self.stop_requested.set()

    def _check_screen(self, number: int) -> None:
        if not 1 <= number <= self.slots:
            raise ValueError("screen is outside configured slot range")

    def _status(self) -> dict:
        with self.lock:
            return status_message(
                self.slots, set(self.registrations), dict(self.sources)
            )

    def _register(self, number: int, client: socket.socket) -> ScreenRegistration:
        self._check_screen(number)
        with self.lock:
            if number in self.registrations:
                raise RuntimeError(f"Screen #{number} is already open")
            registration = ScreenRegistration(number, client)
            self.registrations[number] = registration
            self.sources.pop(number, None)
        registration.send(opened_message(number))
        return registration

    def _drop_registration(self, registration: ScreenRegistration) -> None:
        with self.lock:
            current = self.registrations.get(registration.screen)
            if current is registration:
                self.registrations.pop(registration.screen, None)
                self.sources.pop(registration.screen, None)
        registration.close()

    def _bind(self, number: int, source: ScreenSourceDiscovery) -> None:
        self._check_screen(number)
        with self.lock:
            registration = self.registrations.get(number)
        if registration is None:
            raise RuntimeError(
                f"Screen #{number} is not open; run ./game2/v2/op/screen.sh {number}"
            )
        try:
            registration.send(attach_message(number, source))
        except (EOFError, OSError, ConnectionError):
            self._drop_registration(registration)
            raise RuntimeError(f"Screen #{number} disconnected")
        with self.lock:
            if self.registrations.get(number) is registration:
                self.sources[number] = source

    def _unbind(self, number: int) -> None:
        self._check_screen(number)
        with self.lock:
            registration = self.registrations.get(number)
            self.sources.pop(number, None)
        if registration is not None:
            try:
                registration.send(detach_message(number))
            except (EOFError, OSError, ConnectionError):
                self._drop_registration(registration)

    def _close_screen(self, number: int) -> None:
        self._check_screen(number)
        with self.lock:
            registration = self.registrations.get(number)
            self.sources.pop(number, None)
        if registration is None:
            return
        try:
            registration.send(slot_close_message(number))
        except (EOFError, OSError, ConnectionError):
            pass
        self._drop_registration(registration)

    def _serve_registered(
        self, registration: ScreenRegistration
    ) -> None:
        client = registration.socket
        client.settimeout(1.0)
        try:
            while not self.stop_requested.is_set():
                try:
                    # The foreground Screen normally sends nothing after OPEN.
                    # Reading here exists only to detect EOF when its window closes.
                    recv_frame(client)
                except socket.timeout:
                    continue
                except EOFError:
                    return
                except OSError:
                    return
                except ValueError:
                    return
        finally:
            self._drop_registration(registration)

    def _handle_client(self, client: socket.socket) -> None:
        client.settimeout(2.0)
        keep_open = False
        try:
            request = recv_frame(client)
            kind = decode_screen_server_request(request)
            if kind == OPEN:
                registration = self._register(request["screen"], client)
                keep_open = True
                self._serve_registered(registration)
                return
            if kind == BIND:
                self._bind(
                    request["screen"],
                    ScreenSourceDiscovery.from_dict(request["source"]),
                )
            elif kind == UNBIND:
                self._unbind(request["screen"])
            elif kind == CLOSE:
                self._close_screen(request["screen"])
            elif kind != PROBE:
                raise ValueError("unsupported Screen Server request")
            send_frame(client, self._status())
        except (EOFError, OSError, RuntimeError, ValueError) as exc:
            if not keep_open:
                try:
                    send_frame(client, {
                        "version": 1,
                        "type": "screen_server_error",
                        "message": str(exc),
                    })
                except (OSError, ValueError):
                    pass
            print(f"SCREEN SERVER {type(exc).__name__}: {exc}", flush=True)
        finally:
            if not keep_open:
                try:
                    client.close()
                except OSError:
                    pass

    def start(self, discovery_path=CURRENT_SCREEN_SERVER_PATH):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.host, self.port))
        listener.listen()
        listener.settimeout(0.25)
        self.listener = listener
        host, port = listener.getsockname()[:2]
        self.endpoint = Endpoint(str(host), int(port))
        self.discovery = ScreenServerDiscovery(1, self.endpoint, self.slots)
        self.discovery_path = Path(discovery_path)
        publish_screen_server(self.discovery, self.discovery_path)
        self.ready.set()
        return self.discovery

    def run(self, discovery_path=CURRENT_SCREEN_SERVER_PATH):
        discovery = self.start(discovery_path)
        print("READY " + json.dumps(discovery.to_dict(), sort_keys=True), flush=True)
        workers = []
        try:
            while not self.stop_requested.is_set():
                try:
                    client, _ = self.listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                worker = threading.Thread(
                    target=self._handle_client, args=(client,), daemon=True
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
        if self.listener is not None:
            try:
                self.listener.close()
            except OSError:
                pass
            self.listener = None
        with self.lock:
            registrations = list(self.registrations.values())
            self.registrations.clear()
            self.sources.clear()
        for registration in registrations:
            try:
                registration.send(slot_close_message(registration.screen))
            except (OSError, ValueError, ConnectionError):
                pass
            registration.close()
        if self.discovery is not None and self.discovery_path is not None:
            remove_screen_server(self.discovery, self.discovery_path)
        self.ready.set()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Game2 V2 Screen broker")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--slots", type=int, default=4)
    parser.add_argument("--discovery", default=str(CURRENT_SCREEN_SERVER_PATH))
    args = parser.parse_args(argv)
    server = ScreenServer(host=args.host, port=args.port, slots=args.slots)
    old_int = signal.signal(signal.SIGINT, lambda *_: server.request_stop())
    old_term = signal.signal(signal.SIGTERM, lambda *_: server.request_stop())
    try:
        return server.run(args.discovery)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"ERROR {type(exc).__name__}: {exc}", flush=True)
        return 1
    finally:
        server.close()
        signal.signal(signal.SIGINT, old_int)
        signal.signal(signal.SIGTERM, old_term)


if __name__ == "__main__":
    raise SystemExit(main())
