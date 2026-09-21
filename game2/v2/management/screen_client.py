"""Foreground native Game2 Screen window registered with Screen Server."""
from __future__ import annotations

import argparse
import queue
import socket
import threading

from game2.v2.contracts.framing import recv_frame, send_frame
from game2.v2.contracts.screen import ScreenFrame, ScreenSourceDiscovery, recv_screen_frame
from game2.v2.contracts.screen_server import (
    CURRENT_SCREEN_SERVER_PATH,
    SLOT_ATTACH,
    SLOT_CLOSE,
    SLOT_DETACH,
    SLOT_OPENED,
    ScreenServerDiscovery,
    decode_screen_slot_message,
    open_message,
)


INITIAL_WIDTH = 1280
INITIAL_HEIGHT = 768
WAITING_REDRAW_MS = 250
SOURCE_RECONNECT_MS = 250


class ScreenReceiver:
    def __init__(self, source: ScreenSourceDiscovery):
        self.source = source
        self.socket: socket.socket | None = None
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.done_event = threading.Event()
        self.condition = threading.Condition()
        self.latest: ScreenFrame | None = None
        self.error: BaseException | None = None

    def connect(self) -> None:
        self.socket = socket.create_connection(
            (self.source.endpoint.host, self.source.endpoint.port), timeout=5
        )
        self.socket.settimeout(None)
        self.thread = threading.Thread(
            target=self._read_loop, name="game2-screen-source-receiver", daemon=True
        )
        self.thread.start()

    def _read_loop(self) -> None:
        sock = self.socket
        if sock is None:
            self.done_event.set()
            return
        try:
            while not self.stop_event.is_set():
                frame = recv_screen_frame(sock, self.source.session_id)
                with self.condition:
                    self.latest = frame
                    self.condition.notify_all()
        except (EOFError, OSError, ValueError) as exc:
            if not self.stop_event.is_set():
                self.error = exc
        finally:
            self.done_event.set()
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


class ScreenWindow:
    def __init__(self, screen: int, server: ScreenServerDiscovery):
        if type(screen) is not int or screen <= 0:
            raise ValueError("screen must be positive")
        if screen > server.slots:
            raise ValueError(f"Screen #{screen} is outside Screen Server slot range")
        self.screen_number = screen
        self.server = server
        self.control: socket.socket | None = None
        self.control_thread: threading.Thread | None = None
        self.commands: queue.Queue = queue.Queue()
        self.closed = False
        self.receiver: ScreenReceiver | None = None
        self.source: ScreenSourceDiscovery | None = None
        self.last_tick = -1
        self.status = "Waiting for source..."
        self.next_source_reconnect_ms = 0
        self.window = None
        self.pygame = None
        self.font = None

    def _connect_broker(self) -> None:
        sock = socket.create_connection(
            (self.server.endpoint.host, self.server.endpoint.port), timeout=3
        )
        sock.settimeout(3)
        send_frame(sock, open_message(self.screen_number))
        opened = recv_frame(sock)
        if decode_screen_slot_message(opened, self.screen_number) != SLOT_OPENED:
            sock.close()
            raise RuntimeError("Screen Server did not register the window")
        sock.settimeout(None)
        self.control = sock

        def read_control() -> None:
            try:
                while not self.closed:
                    message = recv_frame(sock)
                    decode_screen_slot_message(message, self.screen_number)
                    self.commands.put(message)
            except (EOFError, OSError, ValueError) as exc:
                if not self.closed:
                    self.commands.put({
                        "type": "broker_error",
                        "message": str(exc) or type(exc).__name__,
                    })

        self.control_thread = threading.Thread(
            target=read_control, name="game2-screen-broker", daemon=True
        )
        self.control_thread.start()

    def _draw_status(self) -> None:
        pygame = self.pygame
        window = self.window
        if pygame is None or window is None:
            return
        width, height = window.get_size()
        window.fill((18, 22, 28))
        label = self.font.render(self.status[:100], True, (230, 230, 230))
        window.blit(label, label.get_rect(center=(width // 2, height // 2)))
        pygame.display.flip()

    def _detach_source(self, status: str = "Waiting for source...") -> None:
        receiver, self.receiver = self.receiver, None
        if receiver is not None:
            receiver.close()
        self.source = None
        self.next_source_reconnect_ms = 0
        self.last_tick = -1
        self.status = status
        self._draw_status()

    def _connect_source(self, source: ScreenSourceDiscovery) -> bool:
        if self.window.get_size() != (source.width, source.height):
            self.window = self.pygame.display.set_mode((source.width, source.height))
            self.pygame.display.set_caption(f"Game2 Screen #{self.screen_number}")
        receiver = ScreenReceiver(source)
        try:
            receiver.connect()
        except OSError as exc:
            self.receiver = None
            self.next_source_reconnect_ms = (
                self.pygame.time.get_ticks() + SOURCE_RECONNECT_MS
            )
            self.status = f"Source reconnecting: {exc}"
            self._draw_status()
            return False
        self.receiver = receiver
        self.next_source_reconnect_ms = 0
        self.status = f"Connected: {source.map_id}"
        self._draw_status()
        return True

    def _attach_source(self, source: ScreenSourceDiscovery) -> None:
        self._detach_source(f"Connecting: {source.map_id}")
        self.source = source
        self._connect_source(source)

    def _source_disconnected(self, detail: str) -> None:
        receiver, self.receiver = self.receiver, None
        if receiver is not None:
            receiver.close()
        self.last_tick = -1
        self.next_source_reconnect_ms = (
            self.pygame.time.get_ticks() + SOURCE_RECONNECT_MS
        )
        self.status = "Source disconnected; reconnecting: " + detail[:70]
        self._draw_status()

    def _handle_commands(self) -> bool:
        while True:
            try:
                message = self.commands.get_nowait()
            except queue.Empty:
                return True
            kind = message.get("type")
            if kind == SLOT_ATTACH:
                self._attach_source(ScreenSourceDiscovery.from_dict(message["source"]))
            elif kind == SLOT_DETACH:
                self._detach_source()
            elif kind == SLOT_CLOSE:
                return False
            elif kind == "broker_error":
                self._detach_source(
                    "Screen Server disconnected: " + message.get("message", "unknown")
                )
        return True

    def run(self) -> int:
        import os
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        import pygame
        self.pygame = pygame
        pygame.display.init()
        pygame.font.init()
        self.font = pygame.font.Font(None, 36)
        self.window = pygame.display.set_mode((INITIAL_WIDTH, INITIAL_HEIGHT))
        pygame.display.set_caption(f"Game2 Screen #{self.screen_number}")
        self._draw_status()
        self._connect_broker()
        print(f"READY screen={self.screen_number}", flush=True)

        clock = pygame.time.Clock()
        last_waiting_redraw = -WAITING_REDRAW_MS
        try:
            while not self.closed:
                for event in pygame.event.get():
                    if event.type == pygame.QUIT or (
                        event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE
                    ):
                        return 0
                if not self._handle_commands():
                    return 0

                receiver = self.receiver
                if receiver is None:
                    now_ms = pygame.time.get_ticks()
                    if (
                        self.source is not None
                        and now_ms >= self.next_source_reconnect_ms
                    ):
                        self._connect_source(self.source)
                        receiver = self.receiver
                    if (
                        receiver is None
                        and now_ms - last_waiting_redraw >= WAITING_REDRAW_MS
                    ):
                        self._draw_status()
                        last_waiting_redraw = now_ms
                if receiver is not None:
                    with receiver.condition:
                        frame = receiver.latest
                    if frame is not None and frame.world_tick > self.last_tick:
                        if self.window.get_size() != (frame.width, frame.height):
                            self.window = pygame.display.set_mode(
                                (frame.width, frame.height)
                            )
                            pygame.display.set_caption(
                                f"Game2 Screen #{self.screen_number}"
                            )
                        surface = pygame.image.fromstring(
                            frame.pixels, (frame.width, frame.height), "RGB"
                        )
                        self.window.blit(surface, (0, 0))
                        pygame.display.flip()
                        self.last_tick = frame.world_tick
                    if receiver.done_event.is_set():
                        detail = (
                            str(receiver.error).strip()
                            if receiver.error is not None else "source closed"
                        )
                        self._source_disconnected(detail)
                clock.tick(60)
        finally:
            self.close()
        return 0

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        receiver, self.receiver = self.receiver, None
        if receiver is not None:
            receiver.close()
        control, self.control = self.control, None
        if control is not None:
            try:
                control.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                control.close()
            except OSError:
                pass
        if (
            self.control_thread is not None
            and self.control_thread is not threading.current_thread()
        ):
            self.control_thread.join(timeout=1)
        if self.pygame is not None:
            self.pygame.quit()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Open one persistent foreground Game2 Screen window"
    )
    parser.add_argument("--screen", type=int, required=True)
    parser.add_argument(
        "--server-discovery", default=str(CURRENT_SCREEN_SERVER_PATH)
    )
    args = parser.parse_args(argv)
    try:
        server = ScreenServerDiscovery.from_file(args.server_discovery)
        return ScreenWindow(args.screen, server).run()
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"ERROR Screen #{args.screen}: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
