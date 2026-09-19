"""Spectator UI for the unified Game2 V2 Training/Exam launcher."""
from __future__ import annotations

import argparse
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from game2.v2.contracts.manifests import PlayerManifest
from game2.v2.contracts.run_events import validate_run_event
from game2.v2.contracts.training_set import TrainingSetManifest
from game2.v2.contracts.vision import VisionFrame
from game2.v2.player.connection import PlayerConnection  # compatibility export for server tooling
from game2.v2.player.peripherals import VisionReceiver


ROOT = Path(__file__).resolve().parents[2]
SETS_ROOT = Path(__file__).resolve().parent / "training" / "sets"
CHECKPOINT_ROOT = Path(__file__).resolve().parent / "runtime" / "checkpoints"
INITIAL_VIEWPORT = (1280, 768)
SIDEBAR_WIDTH = 320
VIEWER_HZ = 60
PALETTE = (
    (17, 27, 40),
    (205, 211, 216),
    (226, 62, 62),
    (59, 132, 255),
    (247, 214, 70),
    (189, 111, 224),
)
_OUTPUT_END = object()

# Existing server smoke tooling used this name. It now deliberately delegates
# to the shared public receiver instead of maintaining a second client.
VisionStream = VisionReceiver


@dataclass(frozen=True)
class TrainingSetEntry:
    path: Path
    manifest: TrainingSetManifest

    @property
    def level(self) -> int:
        return self.manifest.training_set_level


def discover_training_sets(directory: str | Path = SETS_ROOT) -> tuple[TrainingSetEntry, ...]:
    """Discover and sort strict Training Set manifests without a hardcoded list."""
    entries = []
    for path in Path(directory).glob("level-*.json"):
        try:
            manifest = TrainingSetManifest.from_file(path)
        except (OSError, ValueError):
            continue
        entries.append(TrainingSetEntry(path.resolve(), manifest))
    return tuple(sorted(entries, key=lambda item: item.manifest.training_set_level))


@dataclass
class TrainingSetView:
    entry: TrainingSetEntry
    expanded: bool = False
    map_status: dict[str, str] = field(default_factory=dict)
    current_map: str | None = None
    exam_result: str | None = None

    def __post_init__(self) -> None:
        if not self.map_status:
            self.map_status = {item.map_id: "pending" for item in self.entry.manifest.training_maps}

    @property
    def level(self) -> int:
        return self.entry.manifest.training_set_level

    def reset_session(self) -> None:
        self.map_status = {item.map_id: "pending" for item in self.entry.manifest.training_maps}
        self.current_map = None
        self.exam_result = None


class TrainingSetUIState:
    """Pure UI state machine; rendering and subprocesses are separate concerns."""

    def __init__(self, entries: tuple[TrainingSetEntry, ...] | list[TrainingSetEntry]):
        self.sets = [TrainingSetView(entry) for entry in sorted(
            entries, key=lambda item: item.manifest.training_set_level)]
        self.active = False
        self.mode: str | None = None
        self.level: int | None = None
        self.vision_manifest: PlayerManifest | None = None
        self.osd_active = False
        self.osd_started_at = 0.0
        self.osd_delay = 0.0
        self.error: str | None = None

    def _get(self, level: int) -> TrainingSetView:
        for item in self.sets:
            if item.level == level:
                return item
        raise KeyError(level)

    def toggle_set(self, level: int) -> bool:
        if self.active:
            return False
        item = self._get(level)
        item.expanded = not item.expanded
        return item.expanded

    def start_train(self, level: int) -> bool:
        if self.active:
            return False
        item = self._get(level)
        item.reset_session()
        item.expanded = True
        self.active, self.mode, self.level = True, "train", level
        self.error = None
        return True

    def start_exam(self, level: int) -> bool:
        if self.active:
            return False
        item = self._get(level)
        item.expanded = True
        item.exam_result = None
        self.active, self.mode, self.level = True, "exam", level
        self.error = None
        return True

    def apply_event(self, event: dict[str, Any], *, now: float | None = None) -> None:
        validate_run_event(event)
        kind = event["event"]
        if kind == "training_set_started":
            self.active, self.mode, self.level = True, "train", event["level"]
            self._get(event["level"]).expanded = True
        elif kind == "map_started":
            item = self._get(event["level"])
            item.current_map = event["map_id"]
            item.map_status[event["map_id"]] = "current"
        elif kind == "vision_ready":
            self.vision_manifest = PlayerManifest.from_dict(event["player_manifest"])
        elif kind == "map_progress":
            item = self._get(event["level"])
            if event["map_id"] == item.current_map:
                item.map_status[event["map_id"]] = "current"
        elif kind == "map_passed":
            item = self._get(event["level"])
            item.map_status[event["map_id"]] = "passed"
            item.current_map = next((map_id for map_id, status in item.map_status.items()
                                     if status == "pending"), None)
        elif kind == "map_failed":
            item = self._get(event["level"])
            item.map_status[event["map_id"]] = "failed"
            item.current_map = None
        elif kind == "training_set_finished":
            self.active = False
            item = self._get(event["level"])
            item.current_map = None
        elif kind == "exam_countdown":
            self.active, self.mode, self.level = True, "exam", event["level"]
            self.osd_active = True
            self.osd_started_at = time.monotonic() if now is None else now
            self.osd_delay = event["delay_seconds"]
            self._get(event["level"]).expanded = True
        elif kind == "exam_started":
            self.osd_active = False
        elif kind == "exam_finished":
            self.osd_active = False
            self.active = False
            self._get(event["level"]).exam_result = event["result"]
        elif kind == "exam_unavailable":
            self.osd_active = False
            self.active = False
            self._get(event["level"]).exam_result = None
            self.error = "Exam resource not installed"
        elif kind == "run_failed":
            self.osd_active = False
            self.active = False
            self.error = event["message"]

    def osd_visible(self, now: float | None = None) -> bool:
        if not self.osd_active:
            return False
        elapsed = (time.monotonic() if now is None else now) - self.osd_started_at
        if elapsed < 0 or elapsed >= self.osd_delay:
            return False
        phase = elapsed / self.osd_delay
        return phase < 0.2 or 0.4 <= phase < 0.6

    def map_state(self, level: int, map_id: str) -> str:
        return self._get(level).map_status[map_id]


class LauncherProcess:
    """Read only EVENT records from one unified CLI subprocess."""

    def __init__(self, *, popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen):
        self.popen_factory = popen_factory
        self.process: subprocess.Popen | None = None
        self.lines: queue.Queue = queue.Queue()
        self._reader: threading.Thread | None = None

    def start(self, command: list[str]) -> None:
        if self.process is not None and self.process.poll() is None:
            raise RuntimeError("a Training or Exam run is already active")
        self.process = self.popen_factory(
            command,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
        )

        def pump() -> None:
            source = getattr(self.process, "stdout", None)
            if source is not None:
                for line in source:
                    self.lines.put(line)
            self.lines.put(_OUTPUT_END)

        self._reader = threading.Thread(target=pump, name="game2-v2-viewer-events", daemon=True)
        self._reader.start()

    def poll_events(self) -> list[dict[str, Any]]:
        events = []
        while True:
            try:
                line = self.lines.get_nowait()
            except queue.Empty:
                break
            if line is _OUTPUT_END or not isinstance(line, str):
                continue
            if not line.startswith("EVENT "):
                continue
            try:
                event = json.loads(line[6:])
                validate_run_event(event)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                events.append({"event": "run_failed", "message": f"Malformed launcher event: {exc}"})
                continue
            events.append(event)
        return events

    def stop(self) -> None:
        process = self.process
        if process is None or process.poll() is not None:
            return
        pid = getattr(process, "pid", None)
        if type(pid) is int and pid > 0:
            try:
                os.killpg(pid, signal.SIGTERM)
            except OSError:
                pass
        else:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if type(pid) is int and pid > 0:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except OSError:
                    pass
            else:
                process.kill()
            process.wait()


def _checkpoint_dir(level: int, root: Path = CHECKPOINT_ROOT) -> Path:
    return root / f"platformer-level-{level}"


def train_command(entry: TrainingSetEntry, *, checkpoint_root: Path = CHECKPOINT_ROOT,
                  max_episodes: int = 50, episode_limit: int = 1200) -> list[str]:
    return [
        sys.executable, "-m", "game2.v2.run", "train", "--set", str(entry.path),
        "--checkpoint-dir", str(_checkpoint_dir(entry.level, checkpoint_root)),
        "--max-episodes-per-map", str(max_episodes), "--clock-mode", "realtime",
        "--episode-limit", str(episode_limit), "--fresh",
    ]


def exam_command(entry: TrainingSetEntry, *, checkpoint_root: Path = CHECKPOINT_ROOT,
                 exam_root: str | Path | None = None,
                 delay: float = 2.5) -> list[str]:
    command = [
        sys.executable, "-m", "game2.v2.run", "exam", "--set", str(entry.path),
        "--checkpoint-dir", str(_checkpoint_dir(entry.level, checkpoint_root)),
        "--delay", str(delay),
    ]
    if exam_root is not None:
        command.extend(("--exam-root", str(exam_root)))
    return command


def _colorize_frame(pygame_module, frame: VisionFrame):
    surface = pygame_module.image.frombuffer(frame.pixels, (frame.width, frame.height), "P")
    surface.set_palette(PALETTE)
    return surface.convert()


class VisionViewer:
    """One native window that only subscribes to public Vision and launcher events."""

    def __init__(self, entries: tuple[TrainingSetEntry, ...], *, checkpoint_root: Path,
                 max_episodes: int, episode_limit: int, exam_root: str | Path | None,
                 delay: float, pygame_module=None, clock=time.monotonic):
        self.state = TrainingSetUIState(entries)
        self.checkpoint_root = checkpoint_root
        self.max_episodes = max_episodes
        self.episode_limit = episode_limit
        self.exam_root = exam_root
        self.delay = delay
        self.pygame = pygame_module
        self.clock = clock
        self.launcher = LauncherProcess()
        self.stream: VisionReceiver | None = None
        self.window = None
        self.viewport = None
        self.sidebar = None
        self.frame_surface = None
        self.latest_frame: VisionFrame | None = None
        self.started_at = self.clock()
        self.frames_at_start = 0
        self.closed = False
        self._set_rects: dict[int, Any] = {}
        self._train_rects: dict[int, Any] = {}
        self._exam_rects: dict[int, Any] = {}
        self.title_font = self.section_font = self.body_font = None

    def initialize(self) -> None:
        if self.pygame is None:
            os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
            import pygame
            self.pygame = pygame
        pygame = self.pygame
        pygame.display.init()
        pygame.font.init()
        self.title_font = pygame.font.Font(None, 30)
        self.section_font = pygame.font.Font(None, 21)
        self.body_font = pygame.font.Font(None, 18)
        self._resize_viewport(*INITIAL_VIEWPORT)

    def _resize_viewport(self, width: int, height: int) -> None:
        assert self.pygame is not None
        self.window = self.pygame.display.set_mode((width + SIDEBAR_WIDTH, height))
        self.pygame.display.set_caption("Game2 V2 Vision")
        self.viewport = self.pygame.Surface((width, height))
        self.sidebar = self.pygame.Surface((SIDEBAR_WIDTH, height))
        self.viewport.fill(PALETTE[0])

    def _attach_vision(self, event: dict[str, Any]) -> None:
        manifest = PlayerManifest.from_dict(event["player_manifest"])
        if self.stream is not None:
            self.stream.close()
        self.latest_frame = None
        self.frame_surface = None
        self.stream = VisionReceiver(manifest)
        self.stream.connect()
        self.frames_at_start = self.stream.frames_received
        self.started_at = self.clock()

    def _handle_launcher_events(self) -> None:
        for event in self.launcher.poll_events():
            if event["event"] == "vision_ready":
                try:
                    self._attach_vision(event)
                except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
                    self.state.apply_event({"event": "run_failed", "message": str(exc)})
                    self.launcher.stop()
                    continue
            self.state.apply_event(event, now=self.clock())

    def _entry(self, level: int) -> TrainingSetEntry:
        return self.state._get(level).entry

    def _start_train(self, level: int) -> None:
        if not self.state.start_train(level):
            return
        try:
            self.launcher.start(train_command(
                self._entry(level), checkpoint_root=self.checkpoint_root,
                max_episodes=self.max_episodes, episode_limit=self.episode_limit))
        except (OSError, RuntimeError, ValueError) as exc:
            self.state.apply_event({"event": "run_failed", "message": str(exc)})

    def _start_exam(self, level: int) -> None:
        if not self.state.start_exam(level):
            return
        try:
            self.launcher.start(exam_command(
                self._entry(level), checkpoint_root=self.checkpoint_root,
                exam_root=self.exam_root, delay=self.delay))
        except (OSError, RuntimeError, ValueError) as exc:
            self.state.apply_event({"event": "run_failed", "message": str(exc)})

    def _draw_text(self, text: str, font, y: int, color) -> None:
        assert self.sidebar is not None
        self.sidebar.blit(font.render(text, True, color), (18, y))

    def _button(self, rect, label: str, enabled: bool) -> None:
        assert self.sidebar is not None and self.pygame is not None
        bright = (235, 243, 247)
        self.pygame.draw.rect(self.sidebar, (43, 132, 81) if enabled else (63, 76, 89), rect)
        rendered = self.body_font.render(label, True, bright)
        self.sidebar.blit(rendered, rendered.get_rect(center=rect.center))

    def _draw_sidebar(self, frame: VisionFrame | None) -> None:
        assert self.sidebar is not None
        bright, accent, muted = (235, 243, 247), (255, 218, 82), (157, 174, 188)
        passed, failed, current = (119, 224, 151), (245, 118, 118), (255, 218, 82)
        self.sidebar.fill((17, 27, 40))
        self._draw_text("Game2 V2 Vision", self.title_font, 16, bright)
        self._draw_text("TRAINING SETS", self.section_font, 54, accent)
        self._set_rects.clear()
        self._train_rects.clear()
        self._exam_rects.clear()
        y = 82
        for item in self.state.sets:
            self._set_rects[item.level] = self.pygame.Rect(12, y - 4, SIDEBAR_WIDTH - 24, 25)
            marker = "v" if item.expanded else ">"
            self._draw_text(f"{marker} Training Set Level {item.level}", self.body_font, y, bright)
            y += 28
            if not item.expanded:
                continue
            for map_id, status in item.map_status.items():
                if status == "passed":
                    mark, color = "✓", passed
                elif status == "failed":
                    mark, color = "✕", failed
                elif status == "current":
                    mark, color = "▶", current
                else:
                    mark, color = "○", muted
                self._draw_text(f"  {mark} {map_id}", self.body_font, y, color)
                y += 23
            train_rect = self.pygame.Rect(18, y + 2, SIDEBAR_WIDTH - 36, 30)
            self._train_rects[item.level] = train_rect
            self._button(train_rect, f"TRAIN LEVEL {item.level}", not self.state.active)
            y += 41
            self._draw_text("EXAMINATION", self.section_font, y, accent)
            y += 27
            exam_rect = self.pygame.Rect(18, y, SIDEBAR_WIDTH - 36, 30)
            self._exam_rects[item.level] = exam_rect
            self._button(exam_rect, "START EXAM", not self.state.active)
            y += 42
            if item.exam_result:
                self._draw_text(item.exam_result, self.body_font, y,
                                passed if item.exam_result == "PASS" else failed)
                y += 25

        debug_y = max(y + 8, self.sidebar.get_height() - 174)
        self._draw_text("VISION DEBUG", self.section_font, debug_y, accent)
        debug_y += 26
        mode = self.state.mode or "idle"
        self._draw_text(f"Model/run: {mode}", self.body_font, debug_y, bright)
        debug_y += 22
        connected = self.stream is not None and self.stream.connected
        self._draw_text(f"Vision: {'connected' if connected else 'disconnected'}",
                        self.body_font, debug_y, passed if connected else muted)
        debug_y += 22
        tick = frame.world_tick if frame is not None else None
        self._draw_text(f"World tick: {tick if tick is not None else 'waiting'}",
                        self.body_font, debug_y, bright if tick is not None else muted)
        debug_y += 22
        if self.stream is None:
            fps, age = 0.0, None
        else:
            elapsed = max(self.clock() - self.started_at, 1e-9)
            fps = max(0, self.stream.frames_received - self.frames_at_start) / elapsed
            age = (self.clock() - self.stream.latest_received_at
                   if self.stream.latest_received_at is not None else None)
        self._draw_text(f"Vision FPS: {fps:0.1f}", self.body_font, debug_y, bright)
        debug_y += 22
        self._draw_text("Frame age: waiting" if age is None else
                        f"Frame age: {age * 1000:0.0f} ms", self.body_font, debug_y,
                        bright if age is not None else muted)
        if self.state.error:
            self._draw_text(self.state.error[:38], self.body_font, debug_y + 28, failed)

    def _draw(self) -> None:
        assert self.window is not None and self.viewport is not None and self.sidebar is not None
        frame = self.stream.latest if self.stream is not None else None
        if frame is not None:
            if (frame.width, frame.height) != self.viewport.get_size():
                self._resize_viewport(frame.width, frame.height)
            if self.frame_surface is None or self.latest_frame is None \
                    or frame.world_tick > self.latest_frame.world_tick:
                self.frame_surface = _colorize_frame(self.pygame, frame)
                self.latest_frame = frame
        if self.frame_surface is not None:
            self.viewport.blit(self.frame_surface, (0, 0))
        else:
            self.viewport.fill(PALETTE[0])
        if self.state.osd_visible(self.clock()):
            overlay = self.title_font.render("Examination!", True, (255, 218, 82))
            self.viewport.blit(overlay, overlay.get_rect(center=self.viewport.get_rect().center))
        self._draw_sidebar(frame)
        self.window.blit(self.viewport, (0, 0))
        self.window.blit(self.sidebar, (self.viewport.get_width(), 0))
        self.pygame.display.flip()

    def _handle_input(self, event) -> bool:
        if event.type == self.pygame.QUIT:
            return False
        if event.type != self.pygame.MOUSEBUTTONDOWN or event.button != 1:
            return True
        x, y = event.pos
        if x < self.viewport.get_width():
            return True
        local = (x - self.viewport.get_width(), y)
        for level, rect in self._set_rects.items():
            if rect.collidepoint(local):
                self.state.toggle_set(level)
                return True
        for level, rect in self._train_rects.items():
            if rect.collidepoint(local):
                self._start_train(level)
                return True
        for level, rect in self._exam_rects.items():
            if rect.collidepoint(local):
                self._start_exam(level)
                return True
        return True

    def run(self) -> int:
        self.initialize()
        clock = self.pygame.time.Clock()
        try:
            while not self.closed:
                self._handle_launcher_events()
                for event in self.pygame.event.get():
                    if not self._handle_input(event):
                        return 0
                self._draw()
                clock.tick(VIEWER_HZ)
        finally:
            self.close()
        return 0

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.launcher.stop()
        if self.stream is not None:
            self.stream.close()
        if self.pygame is not None:
            self.pygame.quit()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Game2 V2 Training/Exam spectator viewer")
    parser.add_argument("--sets-dir", default=str(SETS_ROOT))
    parser.add_argument("--checkpoint-root", default=str(CHECKPOINT_ROOT))
    parser.add_argument("--max-episodes-per-map", type=int, default=50)
    parser.add_argument("--episode-limit", type=int, default=1200)
    parser.add_argument("--exam-root")
    parser.add_argument("--delay", type=float, default=2.5)
    args = parser.parse_args(argv)
    entries = discover_training_sets(args.sets_dir)
    if not entries:
        print("ERROR no Training Set manifests found", file=sys.stderr, flush=True)
        return 1
    viewer = VisionViewer(
        entries,
        checkpoint_root=Path(args.checkpoint_root).expanduser().resolve(),
        max_episodes=args.max_episodes_per_map,
        episode_limit=args.episode_limit,
        exam_root=args.exam_root,
        delay=args.delay,
    )
    return viewer.run()


if __name__ == "__main__":
    raise SystemExit(main())
