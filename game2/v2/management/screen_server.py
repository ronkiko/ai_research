"""Independent operator Screen Server with numbered detachable windows."""
from __future__ import annotations

import argparse
import json
import select
import signal
import socket
import subprocess
import sys
import threading
from pathlib import Path

from game2.v2.contracts.framing import recv_frame, send_frame
from game2.v2.contracts.manifests import Endpoint
from game2.v2.contracts.screen import ScreenSourceDiscovery
from game2.v2.contracts.screen_server import (
    BIND, CURRENT_SCREEN_SERVER_PATH, PROBE, UNBIND,
    ScreenServerDiscovery, decode_screen_server_request,
    publish_screen_server, remove_screen_server, status_message,
)


def _terminate(process) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _launch_viewer(screen: int, source: ScreenSourceDiscovery):
    command = [
        sys.executable, "-m", "game2.v2.management.screen_client",
        "--screen", str(screen), "--session-id", source.session_id,
        "--host", source.endpoint.host, "--port", str(source.endpoint.port),
        "--width", str(source.width), "--height", str(source.height),
    ]
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    deadline = __import__("time").monotonic() + 5
    ready = ""
    if process.stdout is not None:
        while __import__("time").monotonic() < deadline:
            readable, _, _ = select.select(
                [process.stdout], [], [], deadline - __import__("time").monotonic()
            )
            if readable:
                ready = process.stdout.readline()
                break
    if not ready.startswith("READY "):
        _terminate(process)
        raise RuntimeError("Screen window failed before READY")
    if process.stdout is not None:
        def drain():
            for line in process.stdout:
                print(line, end="", flush=True)
        threading.Thread(target=drain, daemon=True).start()
    return process


class ScreenServer:
    def __init__(self, *, host="127.0.0.1", port=0, slots=4, viewer_launcher=_launch_viewer):
        if type(slots) is not int or slots <= 0:
            raise ValueError("slots must be positive")
        self.host, self.port, self.slots = host, port, slots
        self.viewer_launcher = viewer_launcher
        self.listener = None
        self.endpoint = None
        self.stop_requested = threading.Event()
        self.ready = threading.Event()
        self.discovery = None
        self.discovery_path = None
        self.sources: dict[int, ScreenSourceDiscovery] = {}
        self.viewers: dict[int, object] = {}

    def request_stop(self):
        self.stop_requested.set()

    def _reap(self):
        for number, process in list(self.viewers.items()):
            if process.poll() is not None:
                self.viewers.pop(number, None)
                self.sources.pop(number, None)

    def _status(self):
        self._reap()
        return status_message(self.slots, self.sources)

    def _bind(self, number: int, source: ScreenSourceDiscovery):
        if not 1 <= number <= self.slots:
            raise ValueError("screen is outside configured slot range")
        old = self.viewers.pop(number, None)
        _terminate(old)
        self.sources.pop(number, None)
        process = self.viewer_launcher(number, source)
        self.viewers[number] = process
        self.sources[number] = source

    def _unbind(self, number: int):
        if not 1 <= number <= self.slots:
            raise ValueError("screen is outside configured slot range")
        _terminate(self.viewers.pop(number, None))
        self.sources.pop(number, None)

    def _handle_client(self, client):
        client.settimeout(1)
        try:
            while not self.stop_requested.is_set():
                try:
                    request = recv_frame(client)
                except socket.timeout:
                    continue
                kind = decode_screen_server_request(request)
                if kind == BIND:
                    self._bind(
                        request["screen"],
                        ScreenSourceDiscovery.from_dict(request["source"]),
                    )
                elif kind == UNBIND:
                    self._unbind(request["screen"])
                elif kind != PROBE:
                    raise ValueError("unsupported Screen Server request")
                send_frame(client, self._status())
        except (EOFError, OSError, RuntimeError, ValueError):
            return
        finally:
            try: client.close()
            except OSError: pass

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
        workers=[]
        try:
            while not self.stop_requested.is_set():
                self._reap()
                try:
                    client,_ = self.listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                worker=threading.Thread(target=self._handle_client,args=(client,),daemon=True)
                worker.start(); workers.append(worker)
        finally:
            self.close()
            for worker in workers: worker.join(timeout=1)
        return 0

    def close(self):
        self.stop_requested.set()
        if self.listener is not None:
            try: self.listener.close()
            except OSError: pass
            self.listener=None
        for process in list(self.viewers.values()):
            _terminate(process)
        self.viewers.clear(); self.sources.clear()
        if self.discovery is not None and self.discovery_path is not None:
            remove_screen_server(self.discovery, self.discovery_path)
        self.ready.set()


def main(argv=None):
    parser=argparse.ArgumentParser(description="Game2 V2 independent Screen Server")
    parser.add_argument("--host",default="127.0.0.1")
    parser.add_argument("--port",type=int,default=0)
    parser.add_argument("--slots",type=int,default=4)
    parser.add_argument("--discovery",default=str(CURRENT_SCREEN_SERVER_PATH))
    args=parser.parse_args(argv)
    server=ScreenServer(host=args.host,port=args.port,slots=args.slots)
    old_int=signal.signal(signal.SIGINT,lambda *_:server.request_stop())
    old_term=signal.signal(signal.SIGTERM,lambda *_:server.request_stop())
    try:
        return server.run(args.discovery)
    except (OSError,RuntimeError,TypeError,ValueError) as exc:
        print(f"ERROR {type(exc).__name__}: {exc}",flush=True); return 1
    finally:
        server.close()
        signal.signal(signal.SIGINT,old_int); signal.signal(signal.SIGTERM,old_term)


if __name__=="__main__":
    raise SystemExit(main())
