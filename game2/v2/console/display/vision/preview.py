"""Human spectator renderer for the exact public Grid Vision observation."""
from __future__ import annotations

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
MAJOR_GRID_COLOR = (228, 234, 238)
MINOR_GRID_COLOR = (124, 136, 148)
RULER_BACKGROUND = (5, 9, 14, 190)
RULER_TEXT = (242, 246, 248)
HUD_BACKGROUND = (5, 9, 14, 210)
HUD_TEXT = (242, 246, 248)


class VisionPreviewRenderer:
    """Render only data already present in VisionGrid for human inspection."""

    def __init__(self, world, *, target_surface, pygame_module):
        self.world = world
        self.target_surface = target_surface
        self.pygame = pygame_module
        if target_surface.get_size() != (world.width, world.height):
            raise ValueError("Vision preview target must match World dimensions")
        self._static_signature = None
        self._static_surface = None
        self._font = None

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

    @staticmethod
    def _dashed_vertical(pygame, surface, x: int, height: int) -> None:
        for y in range(0, height, 8):
            pygame.draw.line(
                surface, MINOR_GRID_COLOR,
                (x, y), (x, min(y + 3, height - 1)),
            )

    @staticmethod
    def _dashed_horizontal(pygame, surface, y: int, width: int) -> None:
        for x in range(0, width, 8):
            pygame.draw.line(
                surface, MINOR_GRID_COLOR,
                (x, y), (min(x + 3, width - 1), y),
            )

    def _draw_grid(self, surface, grid: VisionGrid, cell: int) -> None:
        pygame = self.pygame
        for x in range(cell, self.world.width, cell):
            if x % grid.tile_size:
                self._dashed_vertical(pygame, surface, x, self.world.height)
        for y in range(cell, self.world.height, cell):
            if y % grid.tile_size:
                self._dashed_horizontal(pygame, surface, y, self.world.width)
        for x in range(0, self.world.width, grid.tile_size):
            pygame.draw.line(
                surface, MAJOR_GRID_COLOR,
                (x, 0), (x, self.world.height - 1), 2,
            )
        for y in range(0, self.world.height, grid.tile_size):
            pygame.draw.line(
                surface, MAJOR_GRID_COLOR,
                (0, y), (self.world.width - 1, y), 2,
            )

    def _font_for_hud(self):
        pygame = self.pygame
        if not pygame.font.get_init():
            pygame.font.init()
        if self._font is None:
            self._font = pygame.font.Font(None, 18)
        return self._font

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

    def render(self, grid: VisionGrid):
        cell = self._validate_grid(grid)
        self.target_surface.blit(self._static(grid, cell), (0, 0))
        self._draw_metadata(self.target_surface, grid, cell)
        self._draw_grid(self.target_surface, grid, cell)
        self._draw_rulers(self.target_surface, grid, cell)
        self._draw_legend(self.target_surface, grid, cell)
        return self.target_surface

    def close(self) -> None:
        self._static_surface = None
        self._font = None


__all__ = [
    "MAJOR_GRID_COLOR", "MINOR_GRID_COLOR", "PHYSICS_COLORS",
    "VisionPreviewRenderer",
]
