"""Temporary single-window examiner for the public Vision peripheral."""
from __future__ import annotations

import argparse
import json
import os
import queue
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

from game2.v2.contracts.connection import (DETACH, RESPAWN, START, attach_message,
                                            detach_message,
                                            probe_message, respawn_message,
                                            start_message, validate_probe_response)
from game2.v2.contracts.discovery import CURRENT_CONSOLE_PATH, ConsoleDiscovery
from game2.v2.contracts.framing import encode_frame, recv_frame
from game2.v2.contracts.manifests import PeripheralManifest, PlayerManifest
from game2.v2.contracts.vision import VisionFrame, recv_vision_frame


ROOT = Path(__file__).resolve().parents[2]
PLAYER_MODULE = "game2.v2.player.scripted.main"
CONSOLE_READY_TIMEOUT = 10.0
PLAYER_READY_TIMEOUT = 10.0
SIDEBAR_WIDTH = 320
VIEWER_HZ = 60
PALETTE = (
    (17, 27, 40),       # EMPTY
    (205, 211, 216),    # SOLID
    (226, 62, 62),      # HAZARD
    (59, 132, 255),     # SELF
    (247, 214, 70),     # GOAL
    (189, 111, 224),    # OTHER_ACTOR
)
_OUTPUT_END = object()


def _strict_json(text: str):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate READY field: {key}")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError(f"invalid READY number: {value}")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)


class VisionStream:
    """A public Vision subscriber with one newest frame mailbox."""

    def __init__(self, manifest: PlayerManifest, *, socket_factory=socket.create_connection,
                 connect_timeout: float = 5.0):
        if manifest.vision is None:
            raise ValueError("Peripheral manifest has no Vision capability")
        self.manifest = manifest
        self.socket_factory = socket_factory
        self.connect_timeout = connect_timeout
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._closed = threading.Event()
        self._condition = threading.Condition()
        self._error: BaseException | None = None
        self._latest: VisionFrame | None = None
        self.latest_received_at: float | None = None
        self.frames_received = 0

    @property
    def latest(self) -> VisionFrame | None:
        with self._condition:
            return self._latest

    @property
    def connected(self) -> bool:
        with self._condition:
            return self._socket is not None and not self._closed.is_set()

    @property
    def failed(self) -> bool:
        with self._condition:
            return self._error is not None

    def connect(self) -> None:
        with self._condition:
            if self._socket is not None:
                raise RuntimeError("Vision stream is already connected")
            if self._closed.is_set():
                raise RuntimeError("Vision stream is closed")
        deadline = time.monotonic() + self.connect_timeout
        while True:
            try:
                stream = self.socket_factory(
                    (self.manifest.vision.host, self.manifest.vision.port), timeout=1)
                stream.settimeout(None)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)
        with self._condition:
            self._socket = stream
        self._thread = threading.Thread(target=self._read_loop, name="v2-vision-examiner",
                                        daemon=True)
        self._thread.start()

    def _read_loop(self) -> None:
        stream = self._socket
        if stream is None:
            return
        try:
            while not self._closed.is_set():
                frame = recv_vision_frame(stream, self.manifest.session_id)
                with self._condition:
                    self._latest = frame
                    self.latest_received_at = time.monotonic()
                    self.frames_received += 1
                    self._condition.notify_all()
        except (EOFError, OSError, ValueError) as exc:
            if not self._closed.is_set():
                with self._condition:
                    self._error = exc
                    self._condition.notify_all()
        finally:
            with self._condition:
                if self._socket is stream:
                    self._socket = None
                self._condition.notify_all()

    def wait_for_frame(self, timeout: float) -> VisionFrame:
        if timeout <= 0:
            raise ValueError("Vision frame timeout must be positive")
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._latest is None:
                if self._error is not None:
                    raise ConnectionError("Vision stream failed") from self._error
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Vision stream did not provide a frame")
                self._condition.wait(remaining)
            return self._latest

    def close(self) -> None:
        self._closed.set()
        with self._condition:
            stream = self._socket
            self._socket = None
            self._condition.notify_all()
        if stream is not None:
            try:
                stream.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                stream.close()
            except OSError:
                pass
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1)


def _connect(endpoint, timeout=5.0):
    if timeout <= 0:
        raise TimeoutError("Console attach endpoint connection timed out")
    deadline = time.monotonic() + timeout
    last_error = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if last_error is not None:
                raise TimeoutError("Console attach endpoint connection timed out") from last_error
            raise TimeoutError("Console attach endpoint connection timed out")
        try:
            sock = socket.create_connection((endpoint.host, endpoint.port),
                                            timeout=min(1.0, remaining))
            return sock
        except OSError as exc:
            last_error = exc
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Console attach endpoint connection timed out") from exc
            time.sleep(min(0.01, remaining))


class PlayerConnection:
    """One long-lived, Player-scoped lifecycle connection to Console."""

    def __init__(self, discovery: ConsoleDiscovery, *, connect_timeout: float = 5.0):
        self.discovery = discovery
        self.connect_timeout = connect_timeout
        self.manifest: PlayerManifest | None = None
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._closed = threading.Event()
        self._condition = threading.Condition()
        self._error: BaseException | None = None
        self._acks: list[dict] = []
        self._latest_event: dict | None = None
        self._send_lock = threading.Lock()

    @property
    def connected(self) -> bool:
        with self._condition:
            return (self._socket is not None and not self._closed.is_set()
                    and self._error is None)

    @property
    def failed(self) -> bool:
        with self._condition:
            return self._error is not None

    @property
    def error(self) -> BaseException | None:
        with self._condition:
            return self._error

    @property
    def latest_event(self) -> dict | None:
        with self._condition:
            return self._latest_event

    def connect(self) -> PlayerManifest:
        if self.connect_timeout <= 0:
            raise ValueError("Player connection timeout must be positive")
        deadline = time.monotonic() + self.connect_timeout
        sock = None
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Console ATTACH timed out before connecting")
            sock = _connect(self.discovery.attach, remaining)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Console ATTACH timed out before sending")
            sock.settimeout(min(0.25, remaining))
            sock.sendall(encode_frame(attach_message()))
            expected = {"version", "type", "session_id", "player_id", "actor_id",
                        "joystick", "vision"}
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Console ATTACH timed out waiting for PlayerManifest")
                sock.settimeout(min(0.25, remaining))
                try:
                    response = recv_frame(sock)
                except socket.timeout:
                    continue
                if (set(response) != expected or type(response.get("version")) is not int
                        or response.get("version") != 1
                        or response.get("type") != "player_manifest"
                        or response.get("session_id") != self.discovery.session_id):
                    raise ValueError("Console ATTACH response is not a PlayerManifest")
                manifest = PlayerManifest.from_dict({
                    key: response[key] for key in expected
                    if key != "type" and key != "version"
                })
                break
            sock.settimeout(0.25)
        except BaseException:
            if sock is not None:
                sock.close()
            raise
        with self._condition:
            self._socket = sock
            self.manifest = manifest
        self._thread = threading.Thread(target=self._read_loop,
                                        name="v2-player-lifecycle", daemon=True)
        self._thread.start()
        return manifest

    def _read_loop(self) -> None:
        with self._condition:
            sock = self._socket
        if sock is None:
            return
        try:
            while not self._closed.is_set():
                try:
                    message = recv_frame(sock)
                except socket.timeout:
                    continue
                message_type = message.get("type")
                if message_type == "lifecycle_ack":
                    if set(message) != {"version", "type", "event", "status", "world_tick"}:
                        raise ValueError("malformed lifecycle acknowledgement")
                    if (message.get("version") != 1 or
                            message["event"] not in {START, RESPAWN, DETACH}
                            or message["status"] not in {"accepted", "rejected"}
                            or type(message["world_tick"]) is not int
                            or message["world_tick"] < 0):
                        raise ValueError("invalid lifecycle acknowledgement")
                    with self._condition:
                        self._acks.append(message)
                        self._condition.notify_all()
                elif message_type == "player_event":
                    if set(message) != {"version", "type", "event", "world_tick", "result"}:
                        raise ValueError("malformed Player event")
                    if (message.get("version") != 1 or message["event"] != "terminal"
                            or message["result"] not in {"success", "dead", "timeout"}
                            or type(message["world_tick"]) is not int
                            or message["world_tick"] < 0):
                        raise ValueError("invalid Player event")
                    with self._condition:
                        self._latest_event = message
                        self._condition.notify_all()
        except (EOFError, OSError, ValueError) as exc:
            if not self._closed.is_set():
                with self._condition:
                    self._error = exc
                    self._condition.notify_all()
        finally:
            with self._condition:
                if self._socket is sock:
                    self._socket = None
                self._condition.notify_all()

    def _send(self, payload: dict) -> None:
        with self._send_lock:
            with self._condition:
                sock = self._socket
                if sock is None or self._closed.is_set() or self._error is not None:
                    raise ConnectionError("Player lifecycle connection is not available")
            try:
                sock.sendall(encode_frame(payload))
            except (OSError, ValueError) as exc:
                self._fail(exc)
                raise ConnectionError("Player lifecycle send failed") from exc

    def wait_ack(self, event: str, timeout: float = 5.0) -> dict | None:
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                for index, acknowledgement in enumerate(self._acks):
                    if acknowledgement["event"] == event:
                        return self._acks.pop(index)
                if self._error is not None or self._closed.is_set():
                    return None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)

    def request_start(self) -> bool:
        self._send(start_message())
        acknowledgement = self.wait_ack(START)
        return acknowledgement is not None and acknowledgement["status"] == "accepted"

    def request_respawn(self) -> bool:
        self._send(respawn_message())
        acknowledgement = self.wait_ack(RESPAWN)
        return acknowledgement is not None and acknowledgement["status"] == "accepted"

    def detach(self) -> None:
        try:
            if self.connected:
                self._send(detach_message())
        except ConnectionError:
            pass

    def _fail(self, error: BaseException) -> None:
        with self._condition:
            if self._error is None:
                self._error = error
            sock = self._socket
            self._socket = None
            self._condition.notify_all()
        self._closed.set()
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    def close(self) -> None:
        self._closed.set()
        with self._condition:
            sock = self._socket
            self._socket = None
            self._condition.notify_all()
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1)


def player_command(manifest_path: str | Path, python: str | None = None) -> list[str]:
    return [python or sys.executable, "-m", PLAYER_MODULE, "--manifest",
            str(Path(manifest_path).resolve()), "--forever"]


def launch_player(manifest_path: str | Path,
                  popen_factory: Callable[..., subprocess.Popen] | None = None,
                  python: str | None = None) -> subprocess.Popen:
    popen = popen_factory or subprocess.Popen
    return popen(player_command(manifest_path, python), cwd=str(ROOT),
                 start_new_session=True, stdout=subprocess.PIPE,
                 stderr=subprocess.STDOUT, text=True, bufsize=1)


def _queue_output(source, lines: queue.Queue) -> None:
    try:
        for line in source:
            lines.put(line)
    finally:
        lines.put(_OUTPUT_END)


def _pump_output(lines: queue.Queue, destination) -> None:
    while True:
        line = lines.get()
        if line is _OUTPUT_END:
            return
        destination.write(line)
        destination.flush()


def wait_console_ready(process: subprocess.Popen, timeout: float = CONSOLE_READY_TIMEOUT,
                        output=None) -> PeripheralManifest:
    """Compatibility parser for a Console process supplied by demo test tooling."""
    if timeout <= 0:
        raise ValueError("Console READY timeout must be positive")
    source = getattr(process, "stdout", None)
    if source is None:
        raise RuntimeError("Console stdout is unavailable")
    destination = sys.stdout if output is None else output
    lines: queue.Queue = queue.Queue()
    threading.Thread(target=_queue_output, args=(source, lines), daemon=True).start()
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Console did not announce READY")
        try:
            line = lines.get(timeout=min(remaining, 0.1))
        except queue.Empty:
            continue
        if line is _OUTPUT_END:
            raise RuntimeError("Console exited before READY")
        destination.write(line)
        destination.flush()
        if line.startswith("READY "):
            try:
                data = _strict_json(line[6:])
                return PeripheralManifest.from_dict(data)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise RuntimeError("Console READY is not a public PeripheralManifest") from exc


def wait_player_ready(process: subprocess.Popen, expected_session_id: str,
                      timeout: float = PLAYER_READY_TIMEOUT, output=None,
                      shutdown_event: threading.Event | None = None) -> None:
    """Wait for the external Player's public READY after the examiner is visible."""
    if timeout <= 0:
        raise ValueError("Player READY timeout must be positive")
    source = getattr(process, "stdout", None)
    if source is None:
        raise RuntimeError("Player stdout is unavailable")
    destination = sys.stdout if output is None else output
    lines: queue.Queue = queue.Queue()
    threading.Thread(target=_queue_output, args=(source, lines), daemon=True).start()
    deadline = time.monotonic() + timeout
    while True:
        if shutdown_event is not None and shutdown_event.is_set():
            raise KeyboardInterrupt
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Player did not announce READY")
        try:
            line = lines.get(timeout=min(remaining, 0.1))
        except queue.Empty:
            continue
        if line is _OUTPUT_END:
            raise RuntimeError("Player exited before READY")
        destination.write(line)
        destination.flush()
        if not line.startswith("READY "):
            continue
        try:
            data = _strict_json(line[6:])
            if (not isinstance(data, dict)
                    or set(data) not in ({"session_id", "vision"},
                                         {"session_id", "vision", "status"})
                    or data["session_id"] != expected_session_id
                    or data["vision"] is not True
                    or ("status" in data and data["status"] not in {"armed", "ready"})):
                raise ValueError("Player READY fields are invalid")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("Player READY is malformed") from exc
        threading.Thread(target=_pump_output, args=(lines, destination),
                         name="v2-vision-player-output", daemon=True).start()
        return


def _kill_process_group(process: subprocess.Popen) -> None:
    pid = getattr(process, "pid", None)
    if type(pid) is not int or pid <= 0:
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass


def stop_process(process: subprocess.Popen | None, timeout: float = 5.0) -> int | None:
    if process is None:
        return None
    if process.poll() is not None:
        return process.wait()
    process.terminate()
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        _kill_process_group(process)
        return process.wait()


def colorize_frame(pygame_module, frame: VisionFrame):
    """Turn public semantic bytes into a human-only paletted surface."""
    surface = pygame_module.image.frombuffer(frame.pixels, (frame.width, frame.height), "P")
    surface.set_palette(PALETTE)
    return surface.convert()


class VisionExaminer:
    """The only native-window owner; it never sends gameplay messages."""

    def __init__(self, stream: VisionStream, player_process, lifecycle_connection,
                 *, pygame_module=None, shutdown_event: threading.Event | None = None,
                 clock=time.monotonic, sleeper=time.sleep):
        self.stream = stream
        self.player_process = player_process
        self.lifecycle = lifecycle_connection
        self.pygame = pygame_module
        self.shutdown_event = shutdown_event or threading.Event()
        self.clock = clock
        self.sleeper = sleeper
        self.window = None
        self.viewport = None
        self.sidebar = None
        self.frame_surface = None
        self._surface_frame: VisionFrame | None = None
        self._presented_world_tick = -1
        self._rendered_frames = 0
        self._started_at = 0.0
        self._display_initialized = False
        self._closed = False
        self._player_ready = False
        self._actor_started = False
        self._terminal_result: str | None = None
        self._last_event_tick = -1
        self._start_error: str | None = None
        self._vision_frames_at_start = 0
        self.title_font = None
        self.section_font = None
        self.body_font = None

    def _initialize(self, frame: VisionFrame) -> None:
        if self.pygame is None:
            os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
            import pygame
            self.pygame = pygame
        pygame = self.pygame
        pygame.display.init()
        pygame.font.init()
        self._display_initialized = True
        self.title_font = pygame.font.Font(None, 32)
        self.section_font = pygame.font.Font(None, 22)
        self.body_font = pygame.font.Font(None, 20)
        self.window = pygame.display.set_mode((frame.width + SIDEBAR_WIDTH, frame.height))
        pygame.display.set_caption("Game2 V2 Vision")
        self.viewport = pygame.Surface((frame.width, frame.height))
        self.sidebar = pygame.Surface((SIDEBAR_WIDTH, frame.height))
        self._started_at = self.clock()
        self._vision_frames_at_start = self.stream.frames_received

    def initialize(self, frame: VisionFrame) -> None:
        """Show the first public frame before an external Player is launched."""
        if self._display_initialized:
            raise RuntimeError("Vision examiner is already initialized")
        self._initialize(frame)
        self._update_surface(frame)
        self._draw_sidebar(frame)
        assert self.window is not None
        assert self.viewport is not None
        assert self.sidebar is not None
        assert self.frame_surface is not None
        assert self.pygame is not None
        self.viewport.blit(self.frame_surface, (0, 0))
        self.window.blit(self.viewport, (0, 0))
        self.window.blit(self.sidebar, (self.viewport.get_width(), 0))
        self.pygame.display.flip()

    def set_player_process(self, process, *, ready: bool = False) -> None:
        if self.player_process is not None:
            raise RuntimeError("Vision examiner already has a Player process")
        self.player_process = process
        self._player_ready = ready

    def _lifecycle_connected(self) -> bool:
        connected = getattr(self.lifecycle, "connected", None)
        if connected is not None:
            return bool(connected)
        poll = getattr(self.lifecycle, "poll", None)
        return poll is None or poll() is None

    def _poll_lifecycle_event(self) -> None:
        event = getattr(self.lifecycle, "latest_event", None)
        if (not isinstance(event, dict) or event.get("event") != "terminal" or
                event.get("world_tick", -1) <= self._last_event_tick):
            return
        self._last_event_tick = event["world_tick"]
        self._terminal_result = event["result"]
        self._actor_started = True

    def _start(self) -> None:
        if not self._player_ready or (self._actor_started and self._terminal_result is None):
            return
        try:
            accepted = (self.lifecycle.request_respawn() if self._terminal_result is not None
                        else self.lifecycle.request_start())
        except (ConnectionError, OSError, RuntimeError, ValueError) as exc:
            self._start_error = str(exc)
            return
        if accepted:
            self._actor_started = True
            self._terminal_result = None
        else:
            self._start_error = "Console rejected START"

    def _button_rect(self):
        assert self.sidebar is not None
        assert self.pygame is not None
        return self.pygame.Rect(22, self.sidebar.get_height() - 66,
                                self.sidebar.get_width() - 44, 44)

    def _update_surface(self, frame: VisionFrame) -> None:
        if frame.world_tick <= self._presented_world_tick:
            return
        if self.viewport is None or self.sidebar is None:
            raise RuntimeError("Vision examiner is not initialized")
        if frame.width != self.viewport.get_width() or frame.height != self.viewport.get_height():
            raise ValueError("Vision resolution changed during a session")
        self.frame_surface = colorize_frame(self.pygame, frame)
        self._surface_frame = frame
        self._presented_world_tick = frame.world_tick
        self._rendered_frames += 1

    def _draw_text(self, text: str, font, y: int, color) -> None:
        assert self.sidebar is not None
        self.sidebar.blit(font.render(text, True, color), (22, y))

    def _draw_sidebar(self, frame: VisionFrame | None) -> None:
        assert self.sidebar is not None
        assert self.title_font is not None
        assert self.section_font is not None
        assert self.body_font is not None
        bright = (235, 243, 247)
        accent = (255, 218, 82)
        muted = (157, 174, 188)
        self.sidebar.fill((17, 27, 40))
        self._draw_text("Game2 V2 Vision", self.title_font, 26, bright)
        self._draw_text("VISION DEBUG", self.section_font, 82, accent)
        if self.player_process is None or not self._player_ready:
            player_label = "Model: Waiting"
            player_color = muted
        elif self.player_process.poll() is None:
            player_label = "Model: Ready / Armed"
            player_color = (119, 224, 151)
        else:
            player_label = "Model: Exited"
            player_color = (245, 118, 118)
        console_status = "Connected" if self._lifecycle_connected() else "Disconnected"
        console_color = (119, 224, 151) if self._lifecycle_connected() else (245, 118, 118)
        vision_status = "Connected" if self.stream.connected else "Disconnected"
        vision_color = (119, 224, 151) if self.stream.connected else (245, 118, 118)
        self._draw_text(player_label, self.body_font, 120, player_color)
        self._draw_text(f"Console: {console_status}", self.body_font, 148, console_color)
        self._draw_text(f"Vision: {vision_status}", self.body_font, 176, vision_color)
        if self._terminal_result is not None:
            result_label = {"success": "SUCCESS", "dead": "DEAD",
                            "timeout": "TIME OUT"}[self._terminal_result]
            actor_label = f"RESULT: {result_label}"
        elif self._actor_started:
            actor_label = "Actor: Running"
        else:
            actor_label = "Actor: Not spawned"
        self._draw_text(actor_label, self.body_font, 204,
                        (255, 218, 82) if self._terminal_result else bright)
        if frame is not None:
            self._draw_text(f"Resolution: {frame.width} x {frame.height}",
                            self.body_font, 242, bright)
            self._draw_text("Pixel format: u8 semantic", self.body_font, 270, bright)
            self._draw_text(f"World tick: {frame.world_tick}", self.body_font, 298, bright)
        else:
            self._draw_text("Resolution: waiting", self.body_font, 242, muted)
            self._draw_text("Pixel format: u8 semantic", self.body_font, 270, bright)
            self._draw_text("World tick: waiting", self.body_font, 298, muted)
        elapsed = max(self.clock() - self._started_at, 1e-9)
        vision_fps = max(0, self.stream.frames_received - self._vision_frames_at_start) / elapsed
        age = (self.clock() - self.stream.latest_received_at
               if self.stream.latest_received_at is not None else None)
        self._draw_text(f"Frames received: {self.stream.frames_received}",
                        self.body_font, 330, bright)
        self._draw_text(f"Vision FPS: {vision_fps:0.1f}", self.body_font, 358, bright)
        self._draw_text("Latest age: waiting" if age is None else
                        f"Latest age: {age * 1000:0.0f} ms", self.body_font, 386,
                        bright if age is not None else muted)
        self._draw_text("CLASSES", self.section_font, 414, accent)
        for y, line in enumerate(("0 Empty", "1 Solid", "2 Hazard", "3 Self",
                                  "4 Goal", "5 Other Actor"), 1):
            self._draw_text(line, self.body_font, 414 + y * 28, bright)
        button = self._button_rect()
        enabled = self._player_ready and (not self._actor_started or
                                          self._terminal_result is not None)
        assert self.pygame is not None
        self.pygame.draw.rect(self.sidebar, (43, 132, 81) if enabled else (63, 76, 89), button)
        button_label = ("RESPAWN" if self._terminal_result is not None else
                        "START" if enabled else
                        "RUNNING" if self._actor_started else "WAITING FOR MODEL")
        rendered = self.body_font.render(button_label, True, bright)
        self.sidebar.blit(rendered, rendered.get_rect(center=button.center))
        session = self.stream.manifest.session_id[:12]
        player_id = getattr(self.stream.manifest, "player_id", "compatibility-player")
        actor_id = getattr(self.stream.manifest, "actor_id", "compatibility-actor")
        self._draw_text(f"Player: {player_id[:12]}", self.body_font, 620, muted)
        self._draw_text(f"Actor: {actor_id[:12]}", self.body_font, 646, muted)
        self._draw_text(f"Session: {session}", self.body_font, 672, muted)
        if self._start_error:
            self._draw_text(self._start_error[:34], self.body_font, 700, (245, 118, 118))

    def run(self, first_frame_timeout: float = CONSOLE_READY_TIMEOUT) -> int:
        if not self._display_initialized:
            first = self.stream.wait_for_frame(first_frame_timeout)
            self.initialize(first)
        assert self.pygame is not None
        assert self.window is not None
        assert self.viewport is not None
        assert self.sidebar is not None
        assert self.frame_surface is not None
        next_frame = self.clock()
        period = 1 / VIEWER_HZ
        while True:
            if self.shutdown_event.is_set():
                return 0
            if not self._lifecycle_connected():
                print("ERROR Game2 V2 Console connection closed", file=sys.stderr, flush=True)
                return 1
            if getattr(self.stream, "failed", False):
                print("ERROR Game2 V2 Vision connection closed", file=sys.stderr, flush=True)
                return 1
            self._poll_lifecycle_event()
            for event in self.pygame.event.get():
                if event.type == self.pygame.QUIT:
                    return 0
                if event.type == self.pygame.MOUSEBUTTONDOWN and event.button == 1:
                    button = self._button_rect().move(self.viewport.get_width(), 0)
                    if button.collidepoint(event.pos):
                        self._start()
                elif event.type == self.pygame.KEYDOWN and event.key in {
                        self.pygame.K_RETURN, self.pygame.K_SPACE}:
                    self._start()
            latest = self.stream.latest
            if latest is not None:
                self._update_surface(latest)
            if self.frame_surface is not None:
                self.viewport.blit(self.frame_surface, (0, 0))
            self._draw_sidebar(latest)
            self.window.blit(self.viewport, (0, 0))
            self.window.blit(self.sidebar, (self.viewport.get_width(), 0))
            self.pygame.display.flip()
            next_frame += period
            delay = next_frame - self.clock()
            if delay > 0:
                self.sleeper(delay)
            else:
                next_frame = self.clock()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._display_initialized and self.pygame is not None:
            self.pygame.quit()


def _console_is_live(discovery: ConsoleDiscovery) -> bool:
    sock = None
    try:
        sock = socket.create_connection((discovery.attach.host, discovery.attach.port), timeout=0.5)
        sock.settimeout(0.5)
        sock.sendall(encode_frame(probe_message()))
        response = recv_frame(sock)
        validate_probe_response(response, discovery.session_id)
        return response["attach"] == discovery.attach.as_dict()
    except (OSError, EOFError, ValueError):
        return False
    finally:
        if sock is not None:
            sock.close()


def run_vision_demo(*, popen_factory: Callable[..., subprocess.Popen] | None = None,
                    player_factory: Callable[..., subprocess.Popen] | None = None,
                    examiner_factory=VisionExaminer,
                    shutdown_timeout: float = 5.0,
                    startup_timeout: float = CONSOLE_READY_TIMEOUT,
                    discovery_path: str | Path = CURRENT_CONSOLE_PATH) -> int:
    if shutdown_timeout <= 0 or startup_timeout <= 0:
        raise ValueError("demo timeouts must be positive")
    shutdown_event = threading.Event()

    def request_shutdown(_signum, _frame):
        shutdown_event.set()

    previous_int = signal.signal(signal.SIGINT, request_shutdown)
    previous_term = signal.signal(signal.SIGTERM, request_shutdown)
    console = lifecycle = player = None
    stream = examiner = None
    temporary_directory = None
    status = 1
    try:
        temporary_directory = tempfile.TemporaryDirectory(prefix="game2-v2-vision-")
        manifest_path = Path(temporary_directory.name) / "peripheral-manifest.json"
        if popen_factory is not None:
            # Kept only as an injected compatibility seam for the old unit demo.
            console = popen_factory(["legacy-console"], cwd=str(ROOT),
                                    start_new_session=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, bufsize=1)
            manifest = wait_console_ready(console, timeout=startup_timeout)
            lifecycle = console
        else:
            try:
                discovery = ConsoleDiscovery.from_file(discovery_path)
            except (FileNotFoundError, OSError, ValueError):
                print("Game2 V2 Console is not running.\n"
                      "Start ./game2/v2/boot.sh first.", file=sys.stderr, flush=True)
                return 1
            if not _console_is_live(discovery):
                print("Game2 V2 Console is not running.\n"
                      "Start ./game2/v2/boot.sh first.", file=sys.stderr, flush=True)
                return 1
            lifecycle = PlayerConnection(discovery, connect_timeout=startup_timeout)
            manifest = lifecycle.connect()
        manifest.write(manifest_path)
        stream = VisionStream(manifest)
        stream.connect()
        first_frame = stream.wait_for_frame(startup_timeout)
        examiner = examiner_factory(stream, None, lifecycle, shutdown_event=shutdown_event)
        examiner.initialize(first_frame)
        player = launch_player(manifest_path, popen_factory=player_factory)
        wait_player_ready(player, manifest.session_id, timeout=startup_timeout,
                          shutdown_event=shutdown_event)
        examiner.set_player_process(player, ready=True)
        status = examiner.run(first_frame_timeout=startup_timeout)
    except KeyboardInterrupt:
        status = 0
    except (OSError, RuntimeError, TimeoutError, ValueError, ConnectionError) as exc:
        print(f"ERROR Game2 V2 vision demo failed: {exc}", file=sys.stderr, flush=True)
        status = 1
    finally:
        if examiner is not None:
            examiner.close()
        stop_process(player, shutdown_timeout)
        if lifecycle is not None:
            detach = getattr(lifecycle, "detach", None)
            if detach is not None:
                detach()
            close = getattr(lifecycle, "close", None)
            if close is not None:
                close()
        if stream is not None:
            stream.close()
        stop_process(console, shutdown_timeout)
        output = getattr(player, "stdout", None) if player is not None else None
        if output is not None:
            output.close()
        output = getattr(console, "stdout", None) if console is not None else None
        if output is not None:
            output.close()
        if temporary_directory is not None:
            temporary_directory.cleanup()
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)
    return status


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Temporary single-window public Vision examiner")
    parser.add_argument("--shutdown-timeout", type=float, default=5.0)
    parser.add_argument("--startup-timeout", type=float, default=CONSOLE_READY_TIMEOUT)
    parser.add_argument("--discovery", default=str(CURRENT_CONSOLE_PATH))
    args = parser.parse_args(argv)
    return run_vision_demo(shutdown_timeout=args.shutdown_timeout,
                           startup_timeout=args.startup_timeout,
                           discovery_path=args.discovery)


if __name__ == "__main__":
    raise SystemExit(main())
