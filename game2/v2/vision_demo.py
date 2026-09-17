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

from game2.v2.contracts.manifests import PeripheralManifest
from game2.v2.contracts.vision import VisionFrame, recv_vision_frame


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(__file__).resolve().parent / "console" / "configs" / "vision-demo.json"
CONSOLE_MODULE = "game2.v2.console.main"
PLAYER_MODULE = "game2.v2.player.scripted.main"
CONSOLE_READY_TIMEOUT = 10.0
PLAYER_READY_TIMEOUT = 10.0
SIDEBAR_WIDTH = 320
VIEWER_HZ = 60
PALETTE = (
    (17, 27, 40),       # EMPTY
    (205, 211, 216),    # SOLID
    (226, 62, 62),      # HAZARD
    (59, 132, 255),     # AVATAR
    (247, 214, 70),     # GOAL
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

    def __init__(self, manifest: PeripheralManifest, *, socket_factory=socket.create_connection,
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


def console_command(python: str | None = None,
                    config_path: str | Path = CONFIG_PATH) -> list[str]:
    return [python or sys.executable, "-m", CONSOLE_MODULE, "--config",
            str(Path(config_path).resolve())]


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


def launch_console(popen_factory: Callable[..., subprocess.Popen] | None = None,
                   python: str | None = None,
                   config_path: str | Path = CONFIG_PATH) -> subprocess.Popen:
    popen = popen_factory or subprocess.Popen
    return popen(console_command(python, config_path), cwd=str(ROOT), start_new_session=True,
                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)


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
    """Read the public manifest and continue draining Console output."""
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
                    or set(data) != {"session_id", "vision"}
                    or data["session_id"] != expected_session_id
                    or data["vision"] is not True):
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

    def __init__(self, stream: VisionStream, player_process, console_process,
                 *, pygame_module=None, shutdown_event: threading.Event | None = None,
                 clock=time.monotonic, sleeper=time.sleep):
        self.stream = stream
        self.player_process = player_process
        self.console_process = console_process
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
        self._player_ready = player_process is not None
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
            player_label = "Player: Waiting"
            player_color = muted
        elif self.player_process.poll() is None:
            player_label = "Player: Scripted / Running"
            player_color = (119, 224, 151)
        else:
            player_label = "Player: Scripted / Exited"
            player_color = (245, 118, 118)
        vision_status = "Connected" if self.stream.connected else "Disconnected"
        vision_color = (119, 224, 151) if self.stream.connected else (245, 118, 118)
        self._draw_text(player_label, self.body_font, 120, player_color)
        self._draw_text(f"Vision: {vision_status}", self.body_font, 148, vision_color)
        if frame is not None:
            self._draw_text(f"Resolution: {frame.width} x {frame.height}",
                            self.body_font, 190, bright)
            self._draw_text("Pixel format: u8 semantic", self.body_font, 218, bright)
            self._draw_text(f"World tick: {frame.world_tick}", self.body_font, 246, bright)
        else:
            self._draw_text("Resolution: waiting", self.body_font, 190, muted)
            self._draw_text("Pixel format: u8 semantic", self.body_font, 218, bright)
            self._draw_text("World tick: waiting", self.body_font, 246, muted)
        elapsed = max(self.clock() - self._started_at, 1e-9)
        vision_fps = max(0, self.stream.frames_received - self._vision_frames_at_start) / elapsed
        age = (self.clock() - self.stream.latest_received_at
               if self.stream.latest_received_at is not None else None)
        self._draw_text(f"Frames received: {self.stream.frames_received}",
                        self.body_font, 286, bright)
        self._draw_text(f"Vision FPS: {vision_fps:0.1f}", self.body_font, 314, bright)
        self._draw_text("Latest age: waiting" if age is None else
                        f"Latest age: {age * 1000:0.0f} ms", self.body_font, 342,
                        bright if age is not None else muted)
        self._draw_text("CLASSES", self.section_font, 394, accent)
        for y, line in enumerate(("0 Empty", "1 Solid", "2 Hazard", "3 Avatar", "4 Goal"), 1):
            self._draw_text(line, self.body_font, 394 + y * 28, bright)
        session = self.stream.manifest.session_id[:12]
        self._draw_text(f"Session: {session}", self.body_font, 590, muted)

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
            if self.console_process.poll() is not None:
                return 0 if self.console_process.returncode == 0 else 1
            for event in self.pygame.event.get():
                if event.type == self.pygame.QUIT:
                    return 0
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


def run_vision_demo(*, popen_factory: Callable[..., subprocess.Popen] | None = None,
                    player_factory: Callable[..., subprocess.Popen] | None = None,
                    examiner_factory=VisionExaminer,
                    shutdown_timeout: float = 5.0,
                    startup_timeout: float = CONSOLE_READY_TIMEOUT) -> int:
    if shutdown_timeout <= 0 or startup_timeout <= 0:
        raise ValueError("demo timeouts must be positive")
    shutdown_event = threading.Event()

    def request_shutdown(_signum, _frame):
        shutdown_event.set()

    previous_int = signal.signal(signal.SIGINT, request_shutdown)
    previous_term = signal.signal(signal.SIGTERM, request_shutdown)
    console = player = None
    stream = examiner = None
    temporary_directory = None
    status = 1
    try:
        temporary_directory = tempfile.TemporaryDirectory(prefix="game2-v2-vision-")
        manifest_path = Path(temporary_directory.name) / "peripheral-manifest.json"
        console = launch_console(popen_factory=popen_factory)
        manifest = wait_console_ready(console, timeout=startup_timeout)
        if manifest.vision is None:
            raise RuntimeError("Console READY has no public Vision capability")
        manifest.write(manifest_path)
        stream = VisionStream(manifest)
        stream.connect()
        first_frame = stream.wait_for_frame(startup_timeout)
        examiner = examiner_factory(stream, None, console, shutdown_event=shutdown_event)
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
        if stream is not None:
            stream.close()
        stop_process(player, shutdown_timeout)
        stop_process(console, shutdown_timeout)
        for process in (console, player):
            output = getattr(process, "stdout", None) if process is not None else None
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
    args = parser.parse_args(argv)
    return run_vision_demo(shutdown_timeout=args.shutdown_timeout,
                           startup_timeout=args.startup_timeout)


if __name__ == "__main__":
    raise SystemExit(main())
