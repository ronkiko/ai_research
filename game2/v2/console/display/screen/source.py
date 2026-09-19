"""Headless Console-owned source of human-facing Screen frames."""
from __future__ import annotations

import argparse
import json
import socket
import threading
import time

from ...config import ScreenSourceManifest
from ....contracts.framing import recv_frame
from ....contracts.screen import ScreenFrame
from ...world import load_world
from ..view_state import DisplayState
from .publisher import ScreenPublisher
from .renderer import ScreenRenderer


SCREEN_HZ = 30


def _connect(endpoint, timeout: float = 5.0) -> socket.socket:
    deadline = time.monotonic() + timeout
    while True:
        try:
            sock = socket.create_connection((endpoint.host, endpoint.port), timeout=1)
            sock.settimeout(0.25)
            return sock
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


class ScreenSourceService:
    def __init__(self, manifest: ScreenSourceManifest):
        self.manifest = manifest
        self.world = load_world(manifest.world_file)
        self.publisher = ScreenPublisher(
            manifest.screen.host, manifest.screen.port, manifest.session_id
        )
        self.stop_event = threading.Event()
        self.reader_done = threading.Event()
        self.condition = threading.Condition()
        self.latest: DisplayState | None = None
        self.accepted_tick = -1
        self.presented_tick = -1
        self.state_socket: socket.socket | None = None
        self.reader: threading.Thread | None = None
        self.renderer = None
        self.pygame = None

    def _read_loop(self) -> None:
        sock = self.state_socket
        if sock is None:
            self.reader_done.set()
            return
        try:
            while not self.stop_event.is_set():
                try:
                    payload = recv_frame(sock)
                except socket.timeout:
                    continue
                try:
                    view = DisplayState.from_payload(
                        payload, self.manifest.session_id, self.world, None
                    )
                except (TypeError, ValueError):
                    continue
                with self.condition:
                    if view.world_tick > self.accepted_tick:
                        self.accepted_tick = view.world_tick
                        self.latest = view
                        self.condition.notify_all()
        except (EOFError, OSError, ValueError):
            pass
        finally:
            self.reader_done.set()
            with self.condition:
                self.condition.notify_all()

    def _next_view(self) -> DisplayState | None:
        with self.condition:
            if self.latest is None or self.latest.world_tick <= self.presented_tick:
                return None
            return self.latest

    def start(self) -> None:
        self.state_socket = _connect(self.manifest.engine_state)
        self.publisher.start()
        import os
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        import pygame
        self.pygame = pygame
        surface = pygame.Surface((self.world.width, self.world.height))
        self.renderer = ScreenRenderer(
            self.world, target_surface=surface, pygame_module=pygame, self_actor_id=None
        )
        self.reader = threading.Thread(
            target=self._read_loop, name="v2-screen-source-state", daemon=True
        )
        self.reader.start()

    def run(self) -> int:
        self.start()
        print("READY " + json.dumps({
            "session_id": self.manifest.session_id,
            "screen": self.manifest.screen.as_dict(),
        }, sort_keys=True), flush=True)
        period = 1 / SCREEN_HZ
        next_frame = time.monotonic()
        try:
            while not self.stop_event.is_set():
                now = time.monotonic()
                if now < next_frame:
                    time.sleep(next_frame - now)
                next_frame = max(next_frame + period, time.monotonic())
                if self.publisher.subscriber_count() == 0:
                    if self.reader_done.is_set():
                        return 0
                    continue
                view = self._next_view()
                if view is None:
                    if self.reader_done.is_set():
                        return 0
                    continue
                surface = self.renderer.render(view)
                pixels = self.pygame.image.tostring(surface, "RGB")
                self.publisher.publish(ScreenFrame(
                    self.world.width, self.world.height, pixels, view.world_tick
                ))
                self.presented_tick = view.world_tick
        finally:
            self.close()
        return 0

    def close(self) -> None:
        self.stop_event.set()
        if self.state_socket is not None:
            try:
                self.state_socket.close()
            except OSError:
                pass
            self.state_socket = None
        if self.reader is not None and self.reader is not threading.current_thread():
            self.reader.join(timeout=1)
        self.publisher.close()
        if self.renderer is not None:
            self.renderer.close()
            self.renderer = None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Game2 V2 headless Screen source")
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args(argv)
    try:
        return ScreenSourceService(ScreenSourceManifest.from_file(args.manifest)).run()
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"ERROR Screen source failed: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
