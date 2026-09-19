"""Human spectator renderer for public Grid Vision plus presentation overlays."""
from __future__ import annotations

import json
import math
from pathlib import Path

from ....contracts.vision import (
    META_GOAL,
    META_OTHER_ACTOR,
    META_OTHER_CENTER,
    META_SELF,
    META_SELF_CENTER,
    PHYSICS_EMPTY,
    PHYSICS_HAZARD,
    PHYSICS_SOLID,
    VisionGrid,
)


PHYSICS_COLORS = {
    PHYSICS_EMPTY: (14, 18, 24),
    PHYSICS_SOLID: (88, 96, 108),
    PHYSICS_HAZARD: (152, 50, 54),
}
META_GOAL_COLOR = (54, 190, 86, 112)
META_SELF_COLOR = (40, 190, 240, 132)
META_OTHER_COLOR = (196, 84, 220, 118)
SELF_CENTER_COLOR = (255, 226, 72)
OTHER_CENTER_COLOR = (255, 255, 255)
TRAIL_COLOR = (255, 226, 72)
LOGGED_TICK_COLOR = TRAIL_COLOR
REWARD_POSITIVE_COLOR = (72, 220, 92)
REWARD_NEGATIVE_COLOR = (240, 72, 72)
REWARD_ZERO_COLOR = (246, 236, 178)
REWARD_OUTLINE_COLOR = (5, 9, 14)
MAJOR_GRID_COLOR = (228, 234, 238)
MINOR_GRID_COLOR = (124, 136, 148)
RULER_BACKGROUND = (5, 9, 14, 190)
RULER_TEXT = (242, 246, 248)
HUD_BACKGROUND = (5, 9, 14, 210)
HUD_TEXT = (242, 246, 248)
TERMINAL_LABELS = {
    "success": "VICTORY",
    "dead": "DEAD",
    "timeout": "TIMEOUT",
}


class VisionPreviewRenderer:
    """Render VisionGrid with optional human-only terminal status."""

    def __init__(
        self, world, *, target_surface, pygame_module, trajectory_log=None
    ):
        self.world = world
        self.target_surface = target_surface
        self.pygame = pygame_module
        self.trajectory_log = (
            Path(trajectory_log) if trajectory_log is not None else None
        )
        if target_surface.get_size() != (world.width, world.height):
            raise ValueError("Vision preview target must match World dimensions")
        self._static_signature = None
        self._static_surface = None
        self._font = None
        self._terminal_font = None
        self._reward_font = None
        self._trail: list[tuple[int, int]] = []
        self._trail_epoch: int | None = None
        self._trail_tick = -1
        self._trajectory_offset = 0
        self._trajectory_file_id: tuple[int, int] | None = None
        self._trajectory_episode: int | None = None
        self._rated_episode: int | None = None
        self._logged_ticks: dict[int, tuple[int, int]] = {}
        self._rated_ticks: dict[int, tuple[tuple[int, int], float]] = {}

    @staticmethod
    def _cell_size(grid: VisionGrid) -> int:
        cell_size = grid.sensor_cell_size
        rounded = round(cell_size)
        if rounded <= 0 or abs(cell_size - rounded) > 1e-9:
            raise ValueError("Vision preview requires integer pixel sensor cells")
        return rounded

    def _validate_grid(self, grid: VisionGrid) -> int:
        if not isinstance(grid, VisionGrid):
            raise TypeError("VisionPreviewRenderer requires a VisionGrid")
        if (
            grid.columns != self.world.columns
            or grid.rows != self.world.rows
            or grid.tile_size != self.world.tile_size
        ):
            raise ValueError("VisionGrid does not match preview World")
        cell = self._cell_size(grid)
        if (
            grid.fine_columns * cell != self.world.width
            or grid.fine_rows * cell != self.world.height
        ):
            raise ValueError("Vision fine grid does not cover the preview World")
        return cell

    def _build_static(self, grid: VisionGrid, cell: int):
        pygame = self.pygame
        surface = pygame.Surface((self.world.width, self.world.height))
        for index, value in enumerate(grid.physics):
            row, column = divmod(index, grid.physics_columns)
            pygame.draw.rect(
                surface,
                PHYSICS_COLORS[value],
                (column * cell, row * cell, cell, cell),
            )
        return surface

    def _static(self, grid: VisionGrid, cell: int):
        signature = (
            grid.columns,
            grid.rows,
            grid.tile_size,
            grid.subdivisions,
            grid.physics,
        )
        if signature != self._static_signature or self._static_surface is None:
            self._static_surface = self._build_static(grid, cell)
            self._static_signature = signature
        return self._static_surface

    def _draw_grid(self, surface, grid: VisionGrid, cell: int) -> None:
        pygame = self.pygame
        for x in range(cell, self.world.width, cell):
            if x % grid.tile_size:
                pygame.draw.line(
                    surface, MINOR_GRID_COLOR,
                    (x, 0), (x, self.world.height - 1), 1,
                )
        for y in range(cell, self.world.height, cell):
            if y % grid.tile_size:
                pygame.draw.line(
                    surface, MINOR_GRID_COLOR,
                    (0, y), (self.world.width - 1, y), 1,
                )
        for x in range(0, self.world.width, grid.tile_size):
            pygame.draw.line(
                surface, MAJOR_GRID_COLOR,
                (x, 0), (x, self.world.height - 1), 1,
            )
        for y in range(0, self.world.height, grid.tile_size):
            pygame.draw.line(
                surface, MAJOR_GRID_COLOR,
                (0, y), (self.world.width - 1, y), 1,
            )
        pygame.draw.line(
            surface, MAJOR_GRID_COLOR,
            (self.world.width - 1, 0),
            (self.world.width - 1, self.world.height - 1),
            1,
        )
        pygame.draw.line(
            surface, MAJOR_GRID_COLOR,
            (0, self.world.height - 1),
            (self.world.width - 1, self.world.height - 1),
            1,
        )

    def _font_for_hud(self):
        pygame = self.pygame
        if not pygame.font.get_init():
            pygame.font.init()
        if self._font is None:
            self._font = pygame.font.Font(None, 18)
        return self._font

    def _font_for_reward(self):
        pygame = self.pygame
        if not pygame.font.get_init():
            pygame.font.init()
        if self._reward_font is None:
            self._reward_font = pygame.font.Font(None, 20)
            self._reward_font.set_bold(True)
        return self._reward_font

    @staticmethod
    def _trajectory_point(payload: dict) -> tuple[int, tuple[int, int]] | None:
        tick = payload.get("t")
        x = payload.get("x")
        y = payload.get("y")
        if type(tick) is not int:
            return None
        if type(x) not in (int, float) or type(y) not in (int, float):
            return None
        if not math.isfinite(float(x)) or not math.isfinite(float(y)):
            return None
        return tick, (round(float(x)), round(float(y)))

    def _reset_trajectory_annotations(self) -> None:
        self._trajectory_episode = None
        self._rated_episode = None
        self._logged_ticks.clear()
        self._rated_ticks.clear()

    def _consume_trajectory_row(self, payload: dict) -> bool:
        episode_id = payload.get("e")
        if type(episode_id) is not int:
            return False
        if "m" in payload:
            changed = (
                episode_id != self._trajectory_episode
                or bool(self._logged_ticks)
            )
            if episode_id != self._trajectory_episode:
                self._logged_ticks.clear()
            self._trajectory_episode = episode_id
            return changed
        point = self._trajectory_point(payload)
        if point is None:
            return False
        tick, position = point
        if payload.get("k") == "a":
            reward = payload.get("rw")
            if type(reward) not in (int, float) or not math.isfinite(float(reward)):
                return False
            if episode_id != self._rated_episode:
                self._rated_episode = episode_id
                self._rated_ticks.clear()
            item = (position, float(reward))
            changed = self._rated_ticks.get(tick) != item
            self._rated_ticks[tick] = item
            return changed
        if self._trajectory_episode is None:
            self._trajectory_episode = episode_id
        if episode_id != self._trajectory_episode:
            return False
        changed = self._logged_ticks.get(tick) != position
        self._logged_ticks[tick] = position
        return changed

    def refresh_trajectory(self) -> bool:
        if self.trajectory_log is None:
            return False
        try:
            stat = self.trajectory_log.stat()
        except FileNotFoundError:
            return False
        file_id = (stat.st_dev, stat.st_ino)
        changed = False
        if (
            self._trajectory_file_id is not None
            and (
                file_id != self._trajectory_file_id
                or stat.st_size < self._trajectory_offset
            )
        ):
            self._trajectory_offset = 0
            self._reset_trajectory_annotations()
            changed = True
        self._trajectory_file_id = file_id
        with self.trajectory_log.open("rb") as handle:
            handle.seek(self._trajectory_offset)
            data = handle.read()
        if not data:
            return changed
        newline = data.rfind(b"\n")
        if newline < 0:
            return changed
        complete = data[:newline + 1]
        self._trajectory_offset += len(complete)
        for raw_line in complete.splitlines():
            try:
                payload = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                changed = self._consume_trajectory_row(payload) or changed
        return changed

    @staticmethod
    def _reward_visual(reward: float) -> tuple[str, tuple[int, int, int]]:
        if reward > 0:
            return f"+{reward:.3f}", REWARD_POSITIVE_COLOR
        if reward < 0:
            return f"{reward:.3f}", REWARD_NEGATIVE_COLOR
        return "0.000", REWARD_ZERO_COLOR

    def _draw_trajectory_annotations(self, surface) -> None:
        pygame = self.pygame
        for tick, position in sorted(self._logged_ticks.items()):
            rated_here = (
                self._rated_episode == self._trajectory_episode
                and tick in self._rated_ticks
            )
            if not rated_here:
                pygame.draw.circle(surface, LOGGED_TICK_COLOR, position, 2)
        font = self._font_for_reward()
        occupied = []
        for _tick, (position, reward) in sorted(self._rated_ticks.items()):
            label, color = self._reward_visual(reward)
            pygame.draw.circle(surface, color, position, 3)
            glyph = font.render(label, True, color)
            outline = font.render(label, True, REWARD_OUTLINE_COLOR)
            rect = glyph.get_rect(
                topleft=(position[0] + 6, position[1] - 8 - glyph.get_height())
            )
            rect.x = max(2, min(rect.x, self.world.width - rect.width - 2))
            rect.y = max(2, min(rect.y, self.world.height - rect.height - 2))
            attempts = 0
            while any(rect.inflate(2, 2).colliderect(other) for other in occupied):
                if attempts >= 4:
                    break
                rect.y = max(2, rect.y - rect.height - 2)
                attempts += 1
            for dx, dy in (
                (-1, -1), (0, -1), (1, -1), (-1, 0),
                (1, 0), (-1, 1), (0, 1), (1, 1),
            ):
                surface.blit(outline, rect.move(dx, dy))
            surface.blit(glyph, rect)
            occupied.append(rect.copy())

    def _draw_rulers(self, surface, grid: VisionGrid, cell: int) -> None:
        pygame = self.pygame
        font = self._font_for_hud()
        top_height = 20
        left_width = 38
        top = pygame.Surface((self.world.width, top_height), pygame.SRCALPHA)
        left = pygame.Surface((left_width, self.world.height), pygame.SRCALPHA)
        top.fill(RULER_BACKGROUND)
        left.fill(RULER_BACKGROUND)
        surface.blit(top, (0, 0))
        surface.blit(left, (0, 0))

        for x in range(0, self.world.width + 1, cell):
            px = min(x, self.world.width - 1)
            major = x % grid.tile_size == 0
            length = 8 if major else 4
            pygame.draw.line(surface, RULER_TEXT, (px, 0), (px, length))
            if major:
                label = font.render(str(x), True, RULER_TEXT)
                label_x = min(max(2, x + 2), self.world.width - label.get_width() - 2)
                surface.blit(label, (label_x, 6))

        for y in range(0, self.world.height + 1, cell):
            py = min(y, self.world.height - 1)
            major = y % grid.tile_size == 0
            length = 8 if major else 4
            pygame.draw.line(surface, RULER_TEXT, (0, py), (length, py))
            if major:
                label = font.render(str(y), True, RULER_TEXT)
                label_y = min(max(2, y + 2), self.world.height - label.get_height() - 2)
                surface.blit(label, (10, label_y))

    def _draw_metadata(self, surface, grid: VisionGrid, cell: int) -> None:
        pygame = self.pygame
        overlay = pygame.Surface((self.world.width, self.world.height), pygame.SRCALPHA)
        centers: list[tuple[int, int, tuple[int, int, int]]] = []
        for index, flags in enumerate(grid.metadata):
            if not flags:
                continue
            row, column = divmod(index, grid.metadata_columns)
            rect = (column * cell, row * cell, cell, cell)
            if flags & META_GOAL:
                pygame.draw.rect(overlay, META_GOAL_COLOR, rect)
            if flags & META_SELF:
                pygame.draw.rect(overlay, META_SELF_COLOR, rect)
            if flags & META_OTHER_ACTOR:
                pygame.draw.rect(overlay, META_OTHER_COLOR, rect)
            center = (column * cell + cell // 2, row * cell + cell // 2)
            if flags & META_SELF_CENTER:
                centers.append((*center, SELF_CENTER_COLOR))
            if flags & META_OTHER_CENTER:
                centers.append((*center, OTHER_CENTER_COLOR))
        surface.blit(overlay, (0, 0))
        radius = max(2, min(4, cell // 2))
        for x, y, color in centers:
            pygame.draw.circle(surface, color, (x, y), radius)

    def _self_center_pixel(
        self, grid: VisionGrid, cell: int
    ) -> tuple[int, int] | None:
        for index, flags in enumerate(grid.metadata):
            if flags & META_SELF_CENTER:
                row, column = divmod(index, grid.metadata_columns)
                return (
                    column * cell + cell // 2,
                    row * cell + cell // 2,
                )
        return None

    def _draw_trail(
        self, surface, grid: VisionGrid, cell: int, trail_epoch: int
    ) -> None:
        pygame = self.pygame
        if trail_epoch != self._trail_epoch or grid.world_tick <= self._trail_tick:
            self._trail.clear()
            self._trail_epoch = trail_epoch
        center = self._self_center_pixel(grid, cell)
        if center is not None and (not self._trail or self._trail[-1] != center):
            self._trail.append(center)
        self._trail_tick = grid.world_tick
        if len(self._trail) >= 2:
            pygame.draw.lines(surface, TRAIL_COLOR, False, self._trail, 1)

    def _draw_legend(self, surface, grid: VisionGrid, cell: int) -> None:
        pygame = self.pygame
        font = self._font_for_hud()
        lines = (
            f"CNN GRID VISION  tick {grid.world_tick}",
            (
                f"coarse {grid.columns}x{grid.rows} @ {grid.tile_size}px  |  "
                f"fine {grid.fine_columns}x{grid.fine_rows} @ {cell}px"
            ),
            "physics: EMPTY / SOLID / HAZARD",
            "meta: SELF / CENTER / GOAL / OTHER",
        )
        rendered = [font.render(line, True, HUD_TEXT) for line in lines]
        width = max(item.get_width() for item in rendered) + 16
        height = sum(item.get_height() for item in rendered) + 12
        panel = pygame.Surface((width, height), pygame.SRCALPHA)
        panel.fill(HUD_BACKGROUND)
        y = 6
        for item in rendered:
            panel.blit(item, (8, y))
            y += item.get_height()
        surface.blit(panel, (self.world.width - width - 8, self.world.height - height - 8))

    def _draw_terminal_overlay(self, surface, terminal: str | None) -> None:
        if terminal is None:
            return
        if type(terminal) is not str or terminal not in TERMINAL_LABELS:
            raise ValueError("unknown terminal result")
        pygame = self.pygame
        if not pygame.font.get_init():
            pygame.font.init()
        if self._terminal_font is None:
            self._terminal_font = pygame.font.Font(None, 74)
        panel_width = max(1, min(self.world.width - 16, 640))
        panel_height = max(1, min(self.world.height - 16, 190))
        panel = pygame.Surface((panel_width, panel_height), pygame.SRCALPHA)
        panel.fill((8, 18, 30, 214))
        pygame.draw.rect(panel, (224, 235, 238, 225), panel.get_rect(), 1)
        title = self._terminal_font.render(
            TERMINAL_LABELS[terminal], True, (255, 247, 205)
        )
        panel.blit(
            title,
            title.get_rect(center=(panel_width // 2, panel_height // 2)),
        )
        surface.blit(
            panel,
            panel.get_rect(center=(self.world.width // 2, self.world.height // 2)),
        )

    def render(
        self, grid: VisionGrid, terminal: str | None = None, trail_epoch: int = 0
    ):
        cell = self._validate_grid(grid)
        self.refresh_trajectory()
        self.target_surface.blit(self._static(grid, cell), (0, 0))
        self._draw_metadata(self.target_surface, grid, cell)
        self._draw_grid(self.target_surface, grid, cell)
        self._draw_trail(self.target_surface, grid, cell, trail_epoch)
        self._draw_rulers(self.target_surface, grid, cell)
        self._draw_legend(self.target_surface, grid, cell)
        self._draw_terminal_overlay(self.target_surface, terminal)
        self._draw_trajectory_annotations(self.target_surface)
        return self.target_surface

    def close(self) -> None:
        self._static_surface = None
        self._font = None
        self._terminal_font = None
        self._reward_font = None
        self._trail.clear()
        self._trail_epoch = None
        self._trail_tick = -1
        self._trajectory_offset = 0
        self._trajectory_file_id = None
        self._reset_trajectory_annotations()


__all__ = [
    "LOGGED_TICK_COLOR", "MAJOR_GRID_COLOR", "MINOR_GRID_COLOR", "PHYSICS_COLORS",
    "REWARD_NEGATIVE_COLOR", "REWARD_POSITIVE_COLOR", "REWARD_ZERO_COLOR",
    "TERMINAL_LABELS", "TRAIL_COLOR", "VisionPreviewRenderer",
]
