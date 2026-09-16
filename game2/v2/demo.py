"""Temporary developer control for the Game2 V2 screen demo.

This module owns one process only: the canonical V2 Console. It deliberately
does not compose or address any of the Console's private subsystems.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(__file__).resolve().parent / "console" / "configs" / "screen-demo.json"
CONSOLE_MODULE = "game2.v2.console.main"


def console_command(python: str | None = None,
                    config_path: str | Path = CONFIG_PATH) -> list[str]:
    """Build the only child command this temporary wrapper may start."""
    return [python or sys.executable, "-m", CONSOLE_MODULE,
            "--config", str(Path(config_path).resolve())]


def launch_console(popen_factory: Callable[..., subprocess.Popen] | None = None,
                   python: str | None = None,
                   config_path: str | Path = CONFIG_PATH) -> subprocess.Popen:
    """Start Console in its own session so a hard stop can reap descendants."""
    popen = popen_factory or subprocess.Popen
    return popen(console_command(python, config_path), cwd=str(ROOT),
                 start_new_session=True)


def _kill_console_process_group(process: subprocess.Popen) -> None:
    """Kill Console descendants after the Console itself missed graceful stop."""
    pid = getattr(process, "pid", None)
    if type(pid) is not int or pid <= 0:
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        # The process may have exited between wait() and this cleanup.
        pass


def stop_console(process: subprocess.Popen | None, timeout: float = 5.0) -> int | None:
    """Gracefully stop Console, then kill its process group only on timeout."""
    if process is None:
        return None
    if process.poll() is not None:
        return process.wait()
    process.terminate()
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        # kill() makes the expected child lifecycle explicit; killpg() also
        # covers Engine, Controller, or Display if Console is stuck.
        process.kill()
        _kill_console_process_group(process)
        return process.wait()


class DemoControl:
    """A deliberately tiny, temporary control surface with one action."""

    SIZE = (360, 180)
    BUTTON = (96, 96, 168, 52)

    def __init__(self):
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        import pygame

        self.pygame = pygame
        pygame.display.init()
        pygame.font.init()
        self.surface = pygame.display.set_mode(self.SIZE)
        pygame.display.set_caption("Game2 V2 Demo")
        self.title_font = pygame.font.Font(None, 30)
        self.button_font = pygame.font.Font(None, 26)
        self.button = pygame.Rect(self.BUTTON)
        self.closed = False

    def poll_exit(self) -> bool:
        pygame = self.pygame
        return any(
            event.type == pygame.QUIT
            or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE)
            or (event.type == pygame.MOUSEBUTTONUP and event.button == 1
                and self.button.collidepoint(event.pos))
            for event in pygame.event.get()
        )

    def draw(self) -> None:
        pygame = self.pygame
        self.surface.fill((20, 28, 42))
        title = self.title_font.render("Game2 V2 Demo", True, (231, 239, 247))
        self.surface.blit(title, title.get_rect(center=(self.SIZE[0] // 2, 42)))
        pygame.draw.rect(self.surface, (49, 105, 145), self.button, border_radius=8)
        pygame.draw.rect(self.surface, (133, 205, 226), self.button, width=2,
                         border_radius=8)
        label = self.button_font.render("Exit", True, (245, 250, 252))
        self.surface.blit(label, label.get_rect(center=self.button.center))
        pygame.display.flip()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.pygame.display.quit()


def run_demo(*, popen_factory: Callable[..., subprocess.Popen] | None = None,
             control_factory: Callable[[], DemoControl] | None = None,
             shutdown_timeout: float = 5.0,
             poll_interval: float = 1 / 60) -> int:
    """Run the temporary control until Console exits or the operator stops it."""
    if shutdown_timeout <= 0:
        raise ValueError("shutdown timeout must be positive")
    if poll_interval < 0:
        raise ValueError("poll interval must not be negative")

    shutdown_requested = threading.Event()

    def request_shutdown(_signum, _frame):
        shutdown_requested.set()

    previous_int = signal.signal(signal.SIGINT, request_shutdown)
    previous_term = signal.signal(signal.SIGTERM, request_shutdown)
    process = None
    control = None
    status = 1
    try:
        process = launch_console(popen_factory=popen_factory)
        control = (control_factory or DemoControl)()
        while True:
            if process.poll() is not None:
                status = process.wait()
                break
            if shutdown_requested.is_set() or control.poll_exit():
                stop_console(process, shutdown_timeout)
                status = 0
                break
            control.draw()
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        # This also covers an interrupted blocking call in a host terminal.
        if process is not None:
            stop_console(process, shutdown_timeout)
        status = 0
    except BaseException:
        if process is not None and process.poll() is None:
            stop_console(process, shutdown_timeout)
        raise
    finally:
        if process is not None and process.poll() is None:
            stop_console(process, shutdown_timeout)
        if control is not None:
            control.close()
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)
    return status


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Temporary Game2 V2 screen demo")
    parser.add_argument("--shutdown-timeout", type=float, default=5.0,
                        help="seconds to wait for Console before hard kill")
    args = parser.parse_args(argv)
    return run_demo(shutdown_timeout=args.shutdown_timeout)


if __name__ == "__main__":
    raise SystemExit(main())
