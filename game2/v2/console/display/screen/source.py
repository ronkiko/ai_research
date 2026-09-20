"""Headless Console-owned source of human or Grid-Vision spectator frames."""
from __future__ import annotations

import argparse
import json
import socket
import threading
import time
from pathlib import Path

from ...config import ScreenSourceManifest
from ....contracts.framing import recv_frame
from ....contracts.screen import ScreenFrame
from ...world import load_world
from ..view_state import DisplayState
from ..vision.preview import VisionPreviewRenderer
from ..vision.renderer import VisionGridRenderer
from .publisher import ScreenPublisher
from .renderer import ScreenRenderer


SCREEN_HZ = 30
HUD_FONT_SIZE = 24


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
    def __init__(
        self, manifest: ScreenSourceManifest, *, episode_store: str | Path | None = None
    ):
        self.manifest = manifest
        self.episode_store = (
            Path(episode_store) if episode_store is not None else None
        )
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
        self.episode_actor_id: str | None = None
        self.episode_start_tick: int | None = None
        self.first_motion_tick: int | None = None
        self.terminal_tick: int | None = None
        self.previous_result: str | None = None
        self.vision_actor_id: str | None = None
        self.vision_previous_result: str | None = None
        self.vision_trail_epoch = 0
        self.state_socket: socket.socket | None = None
        self.reader: threading.Thread | None = None
        self.renderer = None
        self.grid_renderer = None
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
                        if self.manifest.view == "screen":
                            self._track_episode(view)
                        else:
                            self._track_vision_trail(view)
                        self.accepted_tick = view.world_tick
                        self.latest = view
                        self.condition.notify_all()
        except (EOFError, OSError, ValueError):
            pass
        finally:
            self.reader_done.set()
            with self.condition:
                self.condition.notify_all()

    def _track_vision_trail(self, view: DisplayState) -> None:
        actor = view.self_actor
        if actor is None:
            if self.vision_actor_id is not None:
                self.vision_trail_epoch += 1
            self.vision_actor_id = None
            self.vision_previous_result = None
            return
        new_actor = actor.actor_id != self.vision_actor_id
        respawned = (
            not new_actor
            and self.vision_previous_result is not None
            and actor.result is None
        )
        if new_actor or respawned:
            self.vision_trail_epoch += 1
        self.vision_actor_id = actor.actor_id
        self.vision_previous_result = actor.result

    def _track_episode(self, view: DisplayState) -> None:
        actor = view.self_actor
        if actor is None:
            self.episode_actor_id = None
            self.episode_start_tick = None
            self.first_motion_tick = None
            self.terminal_tick = None
            self.previous_result = None
            return
        new_actor = actor.actor_id != self.episode_actor_id
        respawned = (
            not new_actor
            and self.previous_result is not None
            and actor.result is None
        )
        if new_actor or respawned or self.episode_start_tick is None:
            self.episode_actor_id = actor.actor_id
            self.episode_start_tick = view.world_tick
            self.first_motion_tick = None
            self.terminal_tick = None
        if (
            actor.result is None
            and self.first_motion_tick is None
            and (
                abs(actor.x - self.world.spawn.x) > 0.5
                or abs(actor.vx) > 1e-6
            )
        ):
            self.first_motion_tick = view.world_tick
        if actor.result is not None and self.terminal_tick is None:
            self.terminal_tick = view.world_tick
        self.previous_result = actor.result

    def _hud_lines(self, view: DisplayState) -> tuple[str, ...]:
        actor = view.self_actor
        if actor is None:
            return (f"{view.map_id} | world {view.world_tick}", "actor: waiting")
        with self.condition:
            start_tick = self.episode_start_tick
            first_motion_tick = self.first_motion_tick
            terminal_tick = self.terminal_tick
        effective_tick = terminal_tick if terminal_tick is not None else view.world_tick
        elapsed_ticks = max(0, effective_tick - start_tick) if start_tick is not None else 0
        elapsed_seconds = elapsed_ticks / self.manifest.physics_hz
        if self.manifest.episode_limit is None:
            timer = f"elapsed {elapsed_seconds:.2f}s"
        else:
            left_ticks = max(0, self.manifest.episode_limit - elapsed_ticks)
            timer = (
                f"episode {elapsed_ticks}/{self.manifest.episode_limit} | "
                f"left {left_ticks / self.manifest.physics_hz:.2f}s"
            )
        if first_motion_tick is None or start_tick is None:
            first_move = "first move: waiting"
        else:
            delay_ticks = max(0, first_motion_tick - start_tick)
            first_move = (
                f"first move +{delay_ticks} ticks "
                f"({delay_ticks / self.manifest.physics_hz:.2f}s)"
            )
        state = actor.result.upper() if actor.result is not None else "ACTIVE"
        return (
            f"{view.map_id} | world {view.world_tick} | {timer}",
            (
                f"x {actor.x:.1f} y {actor.y:.1f} | "
                f"vx {actor.vx:.2f} vy {actor.vy:.2f} | {state}"
            ),
            (
                f"PAD RIGHT={int(actor.input_right)} "
                f"A={int(actor.input_jump)}"
            ),
            first_move,
        )

    def _draw_hud(self, surface, view: DisplayState) -> None:
        pygame = self.pygame
        if pygame is None:
            return
        if not pygame.font.get_init():
            pygame.font.init()
        font = pygame.font.Font(None, HUD_FONT_SIZE)
        rendered = [
            font.render(line, True, (245, 245, 245))
            for line in self._hud_lines(view)
        ]
        width = max(item.get_width() for item in rendered) + 20
        height = sum(item.get_height() for item in rendered) + 16
        panel = pygame.Surface((width, height), pygame.SRCALPHA)
        panel.fill((8, 12, 18, 190))
        y = 8
        for item in rendered:
            panel.blit(item, (10, y))
            y += item.get_height()
        surface.blit(panel, (10, 10))

    def _next_view(self, *, force: bool = False) -> DisplayState | None:
        with self.condition:
            if self.latest is None:
                return None
            if not force and self.latest.world_tick <= self.presented_tick:
                return None
            return self.latest

    def _configure_renderer(self, surface, pygame) -> None:
        if self.manifest.view == "vision":
            self.grid_renderer = VisionGridRenderer(self.world)
            self.renderer = VisionPreviewRenderer(
                self.world,
                target_surface=surface,
                pygame_module=pygame,
                episode_store=self.episode_store,
            )
        else:
            self.renderer = ScreenRenderer(
                self.world, target_surface=surface, pygame_module=pygame,
                self_actor_id=None,
            )

    def start(self) -> None:
        self.state_socket = _connect(self.manifest.engine_state)
        self.publisher.start()
        import os
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        import pygame
        self.pygame = pygame
        surface = pygame.Surface((self.world.width, self.world.height))
        self._configure_renderer(surface, pygame)
        self.reader = threading.Thread(
            target=self._read_loop, name="v2-screen-source-state", daemon=True
        )
        self.reader.start()

    def run(self) -> int:
        self.start()
        print("READY " + json.dumps({
            "session_id": self.manifest.session_id,
            "screen": self.manifest.screen.as_dict(),
            "view": self.manifest.view,
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
                episode_data_changed = (
                    self.manifest.view == "vision"
                    and self.renderer is not None
                    and self.renderer.refresh_episode_data()
                )
                view = self._next_view(force=episode_data_changed)
                if view is None:
                    if self.reader_done.is_set():
                        return 0
                    continue
                if self.manifest.view == "vision":
                    grid = self.grid_renderer.render(view)
                    terminal = (
                        view.self_actor.result
                        if view.self_actor is not None
                        else None
                    )
                    surface = self.renderer.render(
                        grid,
                        terminal=terminal,
                        trail_epoch=self.vision_trail_epoch,
                    )
                else:
                    surface = self.renderer.render(view)
                    self._draw_hud(surface, view)
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
        if self.grid_renderer is not None:
            self.grid_renderer.close()
            self.grid_renderer = None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Game2 V2 headless Screen source")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--episode-store")
    args = parser.parse_args(argv)
    try:
        return ScreenSourceService(
            ScreenSourceManifest.from_file(args.manifest),
            episode_store=args.episode_store,
        ).run()
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"ERROR Screen source failed: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
