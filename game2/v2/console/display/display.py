"""STATE-fed Display service with independent screen and vision renderers."""
from __future__ import annotations

import json
import socket
import threading
import time

from ..config import DisplayManifest
from ...contracts.framing import recv_frame
from ...contracts.vision import VISION_CAPTURE_HZ, VisionGrid
from ..world import load_world
from ..transport.publisher import VisionPublisher
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
        self._accepted_world_tick = -1
        self._presented_world_tick = -1
        self._state_condition = threading.Condition()
        self._reader_stop = threading.Event()
        self._reader_done = threading.Event()
        self._reader_thread: threading.Thread | None = None
        self._state_socket: socket.socket | None = None
        self._closed = False
        self.renderer = renderer if renderer is not None else self._create_renderer()
        self.vision_publisher = (
            VisionPublisher(self.manifest.vision.host, self.manifest.vision.port,
                            self.manifest.session_id)
            if self.manifest.vision is not None else None
        )

    def _create_renderer(self):
        if self.manifest.mode == "vision":
            from .vision.renderer import VisionGridRenderer
            return VisionGridRenderer(self.world, self.manifest.self_actor_id)
        from .screen.renderer import ScreenRenderer
        return ScreenRenderer(self.world, self_actor_id=self.manifest.self_actor_id)

    @property
    def latest_state(self):
        """Return the one latest accepted view held by the Display mailbox."""
        with self._state_condition:
            return self._latest_view

    def _accept_view(self, view: DisplayState) -> bool:
        with self._state_condition:
            if view.world_tick <= self._accepted_world_tick:
                return False
            self._accepted_world_tick = view.world_tick
            self._latest_view = view
            self.frames_received += 1
            self._state_condition.notify_all()
            return True

    def ingest(self, snapshot: dict) -> bool:
        """Validate a STATE and replace the single latest-state slot."""
        try:
            view = DisplayState.from_payload(snapshot, self.manifest.session_id, self.world,
                                             self.manifest.self_actor_id)
        except (TypeError, ValueError):
            return False
        return self._accept_view(view)

    def ingest_state(self, state) -> bool:
        """Validate a state-shaped value and replace the latest-state slot."""
        try:
            view = DisplayState.from_state(state, self.manifest.session_id, self.world,
                                           self.manifest.self_actor_id)
        except (TypeError, ValueError):
            return False
        return self._accept_view(view)

    def _next_view(self):
        with self._state_condition:
            if (self._latest_view is None or
                    self._latest_view.world_tick <= self._presented_world_tick):
                return None
            return self._latest_view

    def present_latest(self) -> bool:
        view = self._next_view()
        if view is None:
            return False
        self.latest_frame = self.renderer.render(view)
        if self.vision_publisher is not None:
            if not isinstance(self.latest_frame, VisionGrid):
                raise TypeError("Vision Display renderer must return a VisionGrid")
            self.vision_publisher.publish(self.latest_frame)
        with self._state_condition:
            self._presented_world_tick = max(self._presented_world_tick, view.world_tick)
        self.rendered_frames += 1
        return True

    _present_latest = present_latest

    def consume(self, snapshot: dict) -> bool:
        """Ingest and immediately present one in-process STATE payload."""
        if not self.ingest(snapshot):
            return False
        self.present_latest()
        return True

    def consume_state(self, state) -> bool:
        """In-process helper for an immutable state-shaped value."""
        if not self.ingest_state(state):
            return False
        self.present_latest()
        return True

    def _poll_close(self) -> bool:
        poll_close = getattr(self.renderer, "poll_close", None)
        return bool(poll_close and poll_close())

    def _read_states(self, state_socket: socket.socket) -> None:
        try:
            while not self._reader_stop.is_set():
                try:
                    snapshot = recv_frame(state_socket)
                except socket.timeout:
                    continue
                self.ingest(snapshot)
        except (EOFError, OSError, ValueError):
            pass
        finally:
            self._reader_done.set()
            with self._state_condition:
                self._state_condition.notify_all()

    def _wait_for_update(self, timeout: float = 0.25) -> None:
        with self._state_condition:
            if self._reader_done.is_set() or self._reader_stop.is_set():
                return
            if (self._latest_view is not None and
                    self._latest_view.world_tick > self._presented_world_tick):
                return
            self._state_condition.wait(timeout)

    def start(self) -> None:
        """Start the latest-state reader without starting a presentation loop."""
        if self._closed:
            raise RuntimeError("DisplayService is closed")
        if self._reader_thread is not None:
            raise RuntimeError("DisplayService is already started")
        try:
            self._state_socket = _connect(self.manifest.engine_state)
            if self.vision_publisher is not None:
                self.vision_publisher.start()
            state_socket = self._state_socket
            self._reader_thread = threading.Thread(
                target=self._read_states, args=(state_socket,),
                name="v2-display-state-reader", daemon=True)
            self._reader_thread.start()
        except BaseException:
            self.close()
            raise

    def run(self) -> int:
        self.start()
        print("READY " + json.dumps({"session_id": self.manifest.session_id,
                                     "mode": self.manifest.mode}, sort_keys=True), flush=True)
        next_vision = time.monotonic()
        vision_period = 1 / VISION_CAPTURE_HZ
        try:
            while True:
                if self.manifest.mode == "screen" and self._poll_close():
                    return 0
                if self.manifest.mode == "vision":
                    now = time.monotonic()
                    if now < next_vision:
                        time.sleep(next_vision - now)
                        continue
                    next_vision += vision_period
                    if next_vision < now - vision_period:
                        next_vision = now + vision_period
                self.present_latest()
                if self._reader_done.is_set() and self._next_view() is None:
                    return 0
                if self.manifest.mode == "screen":
                    pace = getattr(self.renderer, "pace", None)
                    if pace:
                        pace()
        except (EOFError, OSError, socket.timeout, ValueError):
            return 0
        finally:
            self.close()
            print(f"DIAGNOSTICS frames_received={self.frames_received} "
                  f"frames_rendered={self.rendered_frames}", flush=True)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._reader_stop.set()
        with self._state_condition:
            self._state_condition.notify_all()
        if self._state_socket:
            try:
                self._state_socket.close()
            except OSError:
                pass
        if self._reader_thread and self._reader_thread is not threading.current_thread():
            self._reader_thread.join(timeout=1)
        if self.vision_publisher is not None:
            self.vision_publisher.close()
        close = getattr(self.renderer, "close", None)
        if close:
            close()
