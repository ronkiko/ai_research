"""Temporary single-window desktop shell for a playable Game2 V2 session."""
from __future__ import annotations

import argparse
import json
import os
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

from game2.v2.console.config import DisplayManifest, OperatorControlManifest
from game2.v2.console.display.display import DisplayService
from game2.v2.console.display.screen.renderer import ScreenRenderer, terminal_label
from game2.v2.console.world import load_world
from game2.v2.contracts.manifests import PeripheralManifest
from game2.v2.demo_control import DemoControlClient
from game2.v2.player.human.client import HumanJoystickClient
from game2.v2.player.human.keyboard import HumanKeyboardInput


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(__file__).resolve().parent / "console" / "configs" / "embedded-demo.json"
CONSOLE_MODULE = "game2.v2.console.main"
CONSOLE_READY_TIMEOUT = 10.0
INPUT_HZ = 120
RENDER_HZ = 60
SIDEBAR_WIDTH = 320
_OUTPUT_END = object()


def console_command(python: str | None = None,
                    config_path: str | Path = CONFIG_PATH,
                    state_capability_path: str | Path | None = None,
                    control_capability_path: str | Path | None = None) -> list[str]:
    """Build the Console command and request private demo capabilities."""
    command = [python or sys.executable, "-m", CONSOLE_MODULE, "--config",
               str(Path(config_path).resolve())]
    if state_capability_path is not None:
        command.extend(("--state-capability", str(Path(state_capability_path).resolve())))
    if control_capability_path is not None:
        command.extend(("--control-capability", str(Path(control_capability_path).resolve())))
    return command


def launch_console(popen_factory: Callable[..., subprocess.Popen] | None = None,
                   python: str | None = None,
                   config_path: str | Path = CONFIG_PATH,
                   state_capability_path: str | Path | None = None,
                   control_capability_path: str | Path | None = None,
                   capture_output: bool = False) -> subprocess.Popen:
    """Start Console in its own session so a hard stop can reap descendants."""
    popen = popen_factory or subprocess.Popen
    kwargs = {"cwd": str(ROOT), "start_new_session": True}
    if capture_output:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                      text=True, bufsize=1)
    return popen(console_command(python, config_path, state_capability_path,
                                 control_capability_path), **kwargs)


def _strict_ready_json(text: str):
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


def parse_console_ready(line: str) -> PeripheralManifest:
    """Validate one public ``READY <json>`` line as a PeripheralManifest."""
    if not isinstance(line, str) or not line.startswith("READY "):
        raise ValueError("Console did not provide a READY line")
    try:
        return PeripheralManifest.from_dict(_strict_ready_json(line[6:]))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Console READY is not a public PeripheralManifest") from exc


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


def _wait_process_ready(process: subprocess.Popen, timeout: float, parser,
                        label: str, output=None,
                        shutdown_event: threading.Event | None = None):
    """Read one READY line while keeping the child stdout pipe drained."""
    if timeout <= 0:
        raise ValueError(f"{label} READY timeout must be positive")
    source = getattr(process, "stdout", None)
    if source is None:
        raise RuntimeError(f"{label} stdout is unavailable")
    destination = sys.stdout if output is None else output
    lines: queue.Queue = queue.Queue()
    threading.Thread(target=_queue_output, args=(source, lines),
                     name=f"v2-demo-{label.lower()}-output", daemon=True).start()
    deadline = time.monotonic() + timeout
    while True:
        if shutdown_event is not None and shutdown_event.is_set():
            raise KeyboardInterrupt
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"{label} did not announce READY")
        try:
            line = lines.get(timeout=min(remaining, 0.1))
        except queue.Empty:
            continue
        if line is _OUTPUT_END:
            raise RuntimeError(f"{label} exited before READY")
        destination.write(line)
        destination.flush()
        if line.startswith("READY"):
            try:
                ready = parser(line)
            except ValueError as exc:
                raise RuntimeError(f"{label} READY is malformed: {exc}") from exc
            threading.Thread(target=_pump_output, args=(lines, destination),
                             name=f"v2-demo-{label.lower()}-output-pump", daemon=True).start()
            return ready


def wait_console_ready(process: subprocess.Popen, timeout: float = CONSOLE_READY_TIMEOUT,
                        output=None, shutdown_event: threading.Event | None = None) -> PeripheralManifest:
    """Read and validate public Console READY while draining its output."""
    return _wait_process_ready(process, timeout, parse_console_ready, "Console", output,
                               shutdown_event)


def wait_state_capability(path: str | Path, timeout: float = CONSOLE_READY_TIMEOUT,
                          shutdown_event: threading.Event | None = None,
                          expected_session_id: str | None = None) -> DisplayManifest:
    """Read the private state capability emitted for this privileged shell."""
    deadline = time.monotonic() + timeout
    path = Path(path)
    while True:
        if shutdown_event is not None and shutdown_event.is_set():
            raise KeyboardInterrupt
        capability = None
        try:
            capability = DisplayManifest.from_file(path)
        except FileNotFoundError:
            capability = None
        except (OSError, ValueError) as exc:
            if path.exists():
                raise RuntimeError(f"embedded state capability is malformed: {exc}") from exc
        if capability is not None:
            if capability.mode != "screen":
                raise ValueError("embedded state capability is not a screen capability")
            if (expected_session_id is not None and
                    capability.session_id != expected_session_id):
                raise ValueError("embedded state capability session does not match Console")
            return capability
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Console did not emit embedded state capability")
        time.sleep(min(0.01, remaining))


def wait_control_capability(path: str | Path, timeout: float = CONSOLE_READY_TIMEOUT,
                            shutdown_event: threading.Event | None = None,
                            expected_session_id: str | None = None) -> OperatorControlManifest:
    """Read the private lifecycle capability emitted for the demo shell."""
    deadline = time.monotonic() + timeout
    path = Path(path)
    while True:
        if shutdown_event is not None and shutdown_event.is_set():
            raise KeyboardInterrupt
        capability = None
        try:
            capability = OperatorControlManifest.from_file(path)
        except FileNotFoundError:
            capability = None
        except (OSError, ValueError) as exc:
            if path.exists():
                raise RuntimeError(f"embedded control capability is malformed: {exc}") from exc
        if capability is not None:
            if (expected_session_id is not None and
                    capability.session_id != expected_session_id):
                raise ValueError("embedded control capability session does not match Console")
            return capability
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Console did not emit embedded control capability")
        time.sleep(min(0.01, remaining))


def _kill_console_process_group(process: subprocess.Popen) -> None:
    """Kill a top-level process group after graceful termination timed out."""
    pid = getattr(process, "pid", None)
    if type(pid) is not int or pid <= 0:
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass


def _stop_process(process: subprocess.Popen | None, timeout: float = 5.0) -> int | None:
    """Gracefully stop one top-level process, then reap it after a hard stop."""
    if process is None:
        return None
    if process.poll() is not None:
        return process.wait()
    process.terminate()
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        _kill_console_process_group(process)
        return process.wait()


def stop_console(process: subprocess.Popen | None, timeout: float = 5.0) -> int | None:
    """Gracefully stop Console and its process group."""
    return _stop_process(process, timeout)


class DemoShell:
    """The sole desktop owner: embedded presentation plus keyboard host."""

    def __init__(self, capability: DisplayManifest, manifest: PeripheralManifest,
                 *, control_capability: OperatorControlManifest | None = None,
                 client=None, control_client=None, pygame_module=None,
                 shutdown_event: threading.Event | None = None,
                 console_process=None, client_factory=HumanJoystickClient,
                 control_client_factory=DemoControlClient,
                 clock=time.monotonic, sleeper=time.sleep):
        if capability.mode != "screen":
            raise ValueError("DemoShell requires a screen state capability")
        if capability.session_id != manifest.session_id:
            raise ValueError("DemoShell capability session does not match Player session")
        if (control_capability is not None and
                control_capability.session_id != manifest.session_id):
            raise ValueError("DemoShell control capability session does not match Player session")
        self.capability = capability
        self.manifest = manifest
        self.client = client if client is not None else client_factory(manifest)
        self.control = (control_client if control_client is not None else
                        (control_client_factory(control_capability)
                         if control_capability is not None else None))
        self.shutdown_event = shutdown_event or threading.Event()
        self.console_process = console_process
        self.clock = clock
        self.sleeper = sleeper
        self.pygame = pygame_module
        self.world = None
        self.window = None
        self.game_surface = None
        self.sidebar_surface = None
        self.renderer = None
        self.display_service = None
        self.keyboard = None
        self.title_font = None
        self.section_font = None
        self.body_font = None
        self._display_initialized = False
        self._closed = False
        self._restart_held = False
        try:
            if not self.client.connected:
                self.client.connect()
            if self.control is not None and not self.control.connected:
                self.control.connect()
            if self.pygame is None:
                os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
                import pygame
                self.pygame = pygame
            self.world = load_world(capability.world_file)
            pygame = self.pygame
            pygame.display.init()
            pygame.font.init()
            self._display_initialized = True
            self.title_font = pygame.font.Font(None, 34)
            self.section_font = pygame.font.Font(None, 23)
            self.body_font = pygame.font.Font(None, 22)
            self.window = pygame.display.set_mode(
                (self.world.width + SIDEBAR_WIDTH, self.world.height))
            pygame.display.set_caption("Game2 V2")
            self.game_surface = pygame.Surface((self.world.width, self.world.height))
            self.sidebar_surface = pygame.Surface((SIDEBAR_WIDTH, self.world.height))
            self.renderer = ScreenRenderer(
                self.world, target_surface=self.game_surface, pygame_module=pygame)
            self.display_service = DisplayService(
                capability, world=self.world, renderer=self.renderer)
            self.keyboard = HumanKeyboardInput(pygame)
            self.display_service.start()
        except BaseException:
            self.close()
            raise

    def _draw_sidebar(self) -> None:
        pygame = self.pygame
        assert pygame is not None
        assert self.sidebar_surface is not None
        assert self.display_service is not None
        assert self.title_font is not None
        assert self.section_font is not None
        assert self.body_font is not None
        self.sidebar_surface.fill((17, 27, 40))
        bright = (235, 243, 247)
        accent = (255, 218, 82)
        lines = (
            ("Game2 V2", self.title_font, 34, bright),
            ("CONTROLS", self.section_font, 112, accent),
            ("D / Right Arrow   Move", self.body_font, 151, bright),
            ("Space / Up / W    Jump", self.body_font, 181, bright),
            ("R                  Restart", self.body_font, 211, bright),
            ("Player: Connected" if self.client.connected else "Player: Disconnected",
             self.body_font, 274, bright if self.client.connected else (245, 118, 118)),
            ("State:", self.section_font, 338, accent),
        )
        state = self.display_service.latest_state
        result = state.self_actor.result if state and state.self_actor else None
        status = terminal_label(result) if result else "Running"
        lines += ((status, self.body_font, 372, bright),)
        for text, font, y, color in lines:
            rendered = font.render(text, True, color)
            self.sidebar_surface.blit(rendered, (24, y))

    def _handle_event(self, event) -> None:
        pygame = self.pygame
        assert pygame is not None
        assert self.keyboard is not None
        focus_lost = getattr(pygame, "WINDOWFOCUSLOST", None)
        active = getattr(pygame, "ACTIVEEVENT", None)
        if ((focus_lost is not None and event.type == focus_lost) or
                (active is not None and event.type == active and
                 getattr(event, "gain", 1) == 0)):
            self._restart_held = False
            self.keyboard.handle_event(event)
            return
        if event.type == pygame.KEYDOWN and event.key == pygame.K_r:
            if not self._restart_held:
                self._restart_held = True
                self.keyboard.clear()
                if self.control is not None:
                    if self.control.failed:
                        raise ConnectionError(
                            f"Demo control transport failed: {self.control.error}")
                    self.control.request_respawn()
            return
        if event.type == pygame.KEYUP and event.key == pygame.K_r:
            self._restart_held = False
            return
        self.keyboard.handle_event(event)

    def run(self) -> int:
        """Run one 120 Hz input / 60 Hz presentation loop."""
        assert self.pygame is not None
        assert self.world is not None
        assert self.window is not None
        assert self.game_surface is not None
        assert self.sidebar_surface is not None
        assert self.display_service is not None
        assert self.keyboard is not None
        pygame = self.pygame
        input_period = 1 / INPUT_HZ
        render_period = 1 / RENDER_HZ
        next_input = self.clock()
        next_render = next_input
        while True:
            if self.shutdown_event.is_set():
                return 0
            if self.console_process is not None and self.console_process.poll() is not None:
                return 0 if self.console_process.returncode == 0 else 1
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return 0
                self._handle_event(event)

            now = self.clock()
            while now >= next_input:
                if self.client.failed:
                    raise ConnectionError(f"Joystick transport failed: {self.client.error}")
                state = self.keyboard.state
                self.client.send_state(state.right, state.jump)
                next_input += input_period
                if next_input < now - input_period * 4:
                    next_input = now + input_period
            if now >= next_render:
                self.display_service.present_latest()
                self._draw_sidebar()
                self.window.blit(self.game_surface, (0, 0))
                self.window.blit(self.sidebar_surface, (self.world.width, 0))
                pygame.display.flip()
                next_render += render_period
                if next_render < now - render_period * 4:
                    next_render = now + render_period
            delay = min(next_input, next_render) - self.clock()
            if delay > 0:
                self.sleeper(delay)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.display_service is not None:
            self.display_service.close()
        if self.control is not None:
            self.control.close()
        if self.client is not None:
            self.client.close()
        if self._display_initialized and self.pygame is not None:
            self.pygame.quit()


def run_demo(*, popen_factory: Callable[..., subprocess.Popen] | None = None,
             shell_factory=DemoShell, shutdown_timeout: float = 5.0,
             startup_timeout: float = CONSOLE_READY_TIMEOUT) -> int:
    """Run Console and the in-process Human Player inside one desktop window."""
    if shutdown_timeout <= 0:
        raise ValueError("shutdown timeout must be positive")
    if startup_timeout <= 0:
        raise ValueError("startup timeout must be positive")

    shutdown_requested = threading.Event()

    def request_shutdown(_signum, _frame):
        shutdown_requested.set()

    previous_int = signal.signal(signal.SIGINT, request_shutdown)
    previous_term = signal.signal(signal.SIGTERM, request_shutdown)
    process = None
    shell = None
    temporary_directory = None
    status = 1
    try:
        temporary_directory = tempfile.TemporaryDirectory(prefix="game2-v2-demo-")
        capability_path = Path(temporary_directory.name) / "embedded-state-capability.json"
        control_capability_path = Path(temporary_directory.name) / "embedded-control-capability.json"
        process = launch_console(popen_factory=popen_factory, capture_output=True,
                                 state_capability_path=capability_path,
                                 control_capability_path=control_capability_path)
        manifest = wait_console_ready(process, timeout=startup_timeout,
                                      shutdown_event=shutdown_requested)
        capability = wait_state_capability(capability_path, timeout=startup_timeout,
                                            shutdown_event=shutdown_requested,
                                             expected_session_id=manifest.session_id)
        control_capability = wait_control_capability(
            control_capability_path, timeout=startup_timeout,
            shutdown_event=shutdown_requested, expected_session_id=manifest.session_id)
        shell = shell_factory(capability, manifest,
                              control_capability=control_capability,
                              shutdown_event=shutdown_requested, console_process=process)
        status = shell.run()
    except KeyboardInterrupt:
        status = 0
    except (OSError, RuntimeError, TimeoutError, ValueError, ConnectionError) as exc:
        print(f"ERROR Game2 V2 demo failed: {exc}", file=sys.stderr, flush=True)
        status = 1
    finally:
        if shell is not None:
            shell.close()
        if process is not None and process.poll() is None:
            stop_console(process, shutdown_timeout)
        output_stream = getattr(process, "stdout", None) if process is not None else None
        if output_stream is not None:
            output_stream.close()
        if temporary_directory is not None:
            temporary_directory.cleanup()
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)
    return status


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Temporary single-window Game2 V2 demo")
    parser.add_argument("--shutdown-timeout", type=float, default=5.0,
                        help="seconds to wait for Console before hard kill")
    parser.add_argument("--startup-timeout", type=float, default=CONSOLE_READY_TIMEOUT,
                        help="seconds to wait for Console READY and state capability")
    args = parser.parse_args(argv)
    return run_demo(shutdown_timeout=args.shutdown_timeout,
                    startup_timeout=args.startup_timeout)


if __name__ == "__main__":
    raise SystemExit(main())
