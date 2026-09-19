"""One native Screen window attached to one read-only Screen source."""
from __future__ import annotations

import argparse
import socket
import threading
import time

from game2.v2.contracts.manifests import Endpoint
from game2.v2.contracts.screen import ScreenFrame, recv_screen_frame


class ScreenReceiver:
    def __init__(self, endpoint: Endpoint, session_id: str):
        self.endpoint = endpoint
        self.session_id = session_id
        self.socket: socket.socket | None = None
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.condition = threading.Condition()
        self.latest: ScreenFrame | None = None
        self.error: BaseException | None = None

    def connect(self) -> None:
        self.socket = socket.create_connection(
            (self.endpoint.host, self.endpoint.port), timeout=5
        )
        self.socket.settimeout(None)
        self.thread = threading.Thread(
            target=self._read_loop, name="game2-screen-window-receiver", daemon=True
        )
        self.thread.start()

    def _read_loop(self) -> None:
        sock = self.socket
        if sock is None:
            return
        try:
            while not self.stop_event.is_set():
                frame = recv_screen_frame(sock, self.session_id)
                with self.condition:
                    self.latest = frame
                    self.condition.notify_all()
        except (EOFError, OSError, ValueError) as exc:
            if not self.stop_event.is_set():
                self.error = exc
        finally:
            self.stop_event.set()
            with self.condition:
                self.condition.notify_all()

    def close(self) -> None:
        self.stop_event.set()
        sock = self.socket
        self.socket = None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout=1)


def run_window(screen: int, endpoint: Endpoint, session_id: str,
               width: int, height: int) -> int:
    receiver = ScreenReceiver(endpoint, session_id)
    receiver.connect()
    import os
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    import pygame
    pygame.display.init()
    if type(width) is not int or width <= 0 or type(height) is not int or height <= 0:
        raise ValueError("Screen dimensions must be positive integers")
    window = pygame.display.set_mode((width, height))
    pygame.display.set_caption(f"Game2 Screen #{screen}")
    window.fill((18, 22, 28))
    if not pygame.font.get_init():
        pygame.font.init()
    font = pygame.font.Font(None, 36)
    label = font.render("Waiting for source...", True, (230, 230, 230))
    window.blit(label, label.get_rect(center=(width // 2, height // 2)))
    pygame.display.flip()
    last_tick = -1
    clock = pygame.time.Clock()
    try:
        print(f"READY screen={screen}", flush=True)
        while not receiver.stop_event.is_set():
            for event in pygame.event.get():
                if event.type == pygame.QUIT or (
                    event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE
                ):
                    return 0
            with receiver.condition:
                frame = receiver.latest
            if frame is not None and frame.world_tick > last_tick:
                if window.get_size() != (frame.width, frame.height):
                    window = pygame.display.set_mode((frame.width, frame.height))
                    pygame.display.set_caption(f"Game2 Screen #{screen}")
                surface = pygame.image.fromstring(
                    frame.pixels, (frame.width, frame.height), "RGB"
                )
                window.blit(surface, (0, 0))
                pygame.display.flip()
                last_tick = frame.world_tick
            if receiver.error is not None:
                raise ConnectionError("Screen source disconnected") from receiver.error
            clock.tick(60)
    finally:
        receiver.close()
        pygame.display.quit()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Game2 V2 Screen window")
    parser.add_argument("--screen", type=int, required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    args = parser.parse_args(argv)
    if args.screen <= 0:
        parser.error("--screen must be positive")
    try:
        return run_window(
            args.screen, Endpoint(args.host, args.port), args.session_id,
            args.width, args.height,
        )
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError, TypeError, ValueError, ConnectionError) as exc:
        print(f"ERROR Screen #{args.screen}: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
