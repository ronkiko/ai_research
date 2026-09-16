"""STATE-fed Display service with independent screen and vision renderers."""
from __future__ import annotations

import json
import socket
import time

from ..config import DisplayManifest
from ...contracts.framing import recv_frame
from ..world import load_world
from .view_state import DisplayState


def _connect(endpoint, timeout=5.0):
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


class DisplayService:
    """Consumes the latest valid STATE and never sends gameplay messages."""

    def __init__(self, manifest: DisplayManifest, world=None, renderer=None):
        self.manifest = manifest
        self.world = world if world is not None else load_world(manifest.world_file)
        self.frames_received = 0
        self.rendered_frames = 0
        self.latest_frame = None
        self._latest_view = None
        self._state_socket: socket.socket | None = None
        self.renderer = renderer if renderer is not None else self._create_renderer()

    def _create_renderer(self):
        if self.manifest.mode == "vision":
            from .vision.renderer import VisionRenderer
            return VisionRenderer(self.world)
        from .screen.renderer import ScreenRenderer
        return ScreenRenderer(self.world)

    def consume(self, snapshot: dict) -> bool:
        """Validate and render one STATE payload; invalid input is discarded."""
        try:
            view = DisplayState.from_payload(snapshot, self.manifest.session_id, self.world)
        except (TypeError, ValueError):
            return False
        self._latest_view = view
        self.frames_received += 1
        self.latest_frame = self.renderer.render(view)
        self.rendered_frames += 1
        return True

    def consume_state(self, state) -> bool:
        """Test/in-process helper for an immutable state-shaped value."""
        try:
            view = DisplayState.from_state(state, self.manifest.session_id, self.world)
        except (TypeError, ValueError):
            return False
        self._latest_view = view
        self.frames_received += 1
        self.latest_frame = self.renderer.render(view)
        self.rendered_frames += 1
        return True

    def _poll_close(self) -> bool:
        poll_close = getattr(self.renderer, "poll_close", None)
        return bool(poll_close and poll_close())

    def run(self) -> int:
        self._state_socket = _connect(self.manifest.engine_state)
        state_socket = self._state_socket
        print("READY " + json.dumps({"session_id": self.manifest.session_id,
                                     "mode": self.manifest.mode}, sort_keys=True), flush=True)
        try:
            while True:
                if self.manifest.mode == "screen" and self._poll_close():
                    return 0
                try:
                    snapshot = recv_frame(state_socket)
                except socket.timeout:
                    continue
                if not self.consume(snapshot):
                    continue
                if self.manifest.mode == "screen":
                    pace = getattr(self.renderer, "pace", None)
                    if pace:
                        pace()
        except (EOFError, OSError, socket.timeout, ValueError):
            return 0
        finally:
            if self._state_socket:
                self._state_socket.close()
            close = getattr(self.renderer, "close", None)
            if close:
                close()
            print(f"DIAGNOSTICS frames_received={self.frames_received} "
                  f"frames_rendered={self.rendered_frames}", flush=True)
