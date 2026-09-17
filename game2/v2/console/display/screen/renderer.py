"""Human-facing Pygame renderer for one immutable world definition."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from ...world import TileID, WorldDefinition
from ..view_state import DisplayState
from .autotile import AVAILABLE_ATLAS_CELLS, AutoTiler, NeighborMask


ASSET_DIR = Path(__file__).with_name("assets")
SOURCE_TILE_SIZE = 16
DECORATION_ATLAS = {
    "tree": (16, 0, 80, 112),
    "bush": (112, 96, 48, 16),
    "ruin": (176, 80, 32, 32),
}
DECORATION_SCALE = 2
SPIKE_HEIGHT = 26
TERMINAL_LABELS = {
    "success": "VICTORY",
    "dead": "GAME OVER",
    "timeout": "TIME OUT",
}


def terminal_label(terminal: str | None) -> str | None:
    """Return the human-facing result label for a validated terminal state."""
    if terminal is None:
        return None
    if type(terminal) is not str or terminal not in TERMINAL_LABELS:
        raise ValueError("unknown terminal result")
    return TERMINAL_LABELS[terminal]


class ScreenRenderer:
    """A presentation-only viewport with a cached static scene."""

    FPS = 60

    def __init__(self, world: WorldDefinition, fps: int = FPS,
                 target_surface=None, pygame_module=None):
        if type(fps) is not int or fps <= 0:
            raise ValueError("screen fps must be a positive integer")
        self.world = world
        self.width, self.height = world.width, world.height
        self.fps = fps
        self.owns_display = target_surface is None
        self.target_surface = target_surface
        self.pygame = pygame_module
        self.screen = target_surface
        self.static_scene = None
        self.clock = None
        self._closed = False
        self._terminal_fonts = None
        try:
            if self.pygame is None:
                import os
                os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
                import pygame
                self.pygame = pygame
            pygame = self._pygame()
            if self.owns_display:
                pygame.display.init()
                self.screen = pygame.display.set_mode((self.width, self.height))
                pygame.display.set_caption("Game2 V2")
                self.clock = pygame.time.Clock()
            elif self.screen is None or self.screen.get_size() != (self.width, self.height):
                raise ValueError("embedded screen target must match World dimensions")
            self.static_scene = self._build_static_scene()
        except BaseException:
            self.close()
            raise

    def _load(self, name: str):
        path = ASSET_DIR / name
        if not path.is_file():
            raise FileNotFoundError(f"screen asset is missing: {path}")
        pygame = self._pygame()
        image = pygame.image.load(str(path))
        return image.convert_alpha() if self._has_display_surface() else image

    def _scale_pixel_art(self, source, width: int, height: int):
        """Scale without filtering so copied pixel-art keeps hard edges."""
        return self._pygame().transform.scale(source, (width, height))

    def _pygame(self):
        if self.pygame is None:
            raise RuntimeError("Pygame is not initialized")
        return self.pygame

    def _has_display_surface(self) -> bool:
        display = getattr(self._pygame(), "display", None)
        get_surface = getattr(display, "get_surface", None)
        return bool(get_surface and get_surface() is not None)

    def _build_background(self):
        pygame = self._pygame()
        background = pygame.Surface((self.width, self.height))
        if self._has_display_surface():
            background = background.convert()
        background.fill((80, 130, 200))
        scale = self.world.tile_size // SOURCE_TILE_SIZE
        for name in ("BG1.png", "BG2.png", "BG3.png"):
            source = self._load(name)
            image = self._scale_pixel_art(
                source, source.get_width() * scale, source.get_height() * scale)
            for x in range(0, self.width, image.get_width()):
                background.blit(image, (x, self.height - image.get_height()))
        return background

    def _build_terrain(self):
        pygame = self._pygame()
        layer = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        atlas = self._load("Tileset.png")
        tiles = {}
        for cell_x, cell_y in sorted(AVAILABLE_ATLAS_CELLS):
            source = atlas.subsurface((cell_x * SOURCE_TILE_SIZE,
                                       cell_y * SOURCE_TILE_SIZE,
                                       SOURCE_TILE_SIZE, SOURCE_TILE_SIZE))
            tiles[cell_x, cell_y] = self._scale_pixel_art(
                source, self.world.tile_size, self.world.tile_size)

        autotiler = AutoTiler()
        for row, cells in enumerate(self.world.tiles):
            for column, tile in enumerate(cells):
                if tile is not TileID.SOLID:
                    continue
                x, y = column * self.world.tile_size, row * self.world.tile_size
                rect = pygame.Rect(x, y, self.world.tile_size, self.world.tile_size)
                variant = autotiler.variant_for(self.world.tiles, row, column)
                layer.fill((53, 29, 40), rect)
                layer.blit(tiles[variant.atlas_cell], rect)
        return layer

    def _build_hazards(self):
        pygame = self._pygame()
        layer = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        autotiler = AutoTiler()
        for row, cells in enumerate(self.world.tiles):
            for column, tile in enumerate(cells):
                if tile is not TileID.HAZARD:
                    continue
                x, y = column * self.world.tile_size, row * self.world.tile_size
                rect = pygame.Rect(x, y, self.world.tile_size, self.world.tile_size)
                layer.fill((70, 30, 43), rect)
                variant = autotiler.variant_for(self.world.tiles, row, column)
                if variant.neighbor_mask & NeighborMask.NORTH:
                    continue
                for offset in range(0, self.world.tile_size, SOURCE_TILE_SIZE):
                    spike_x = x + offset
                    pygame.draw.polygon(
                        layer, (245, 232, 194),
                        [(spike_x + 2, y + SPIKE_HEIGHT),
                         (spike_x + 8, y + 3),
                         (spike_x + 14, y + SPIKE_HEIGHT)],
                    )
                    pygame.draw.line(
                        layer, (191, 53, 58),
                        (spike_x + 8, y + 3),
                        (spike_x + 11, y + SPIKE_HEIGHT - 5), 2,
                    )
        return layer

    def _build_decorations(self):
        pygame = self._pygame()
        layer = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        atlas = self._load("Decors.png")
        sprites = {}
        for name, source_rect in DECORATION_ATLAS.items():
            source = atlas.subsurface(source_rect)
            sprites[name] = self._scale_pixel_art(
                source, source_rect[2] * DECORATION_SCALE,
                source_rect[3] * DECORATION_SCALE)
        for decoration in self.world.decorations:
            sprite = sprites[decoration.sprite]
            sprite.set_alpha(220)
            layer.blit(sprite, (decoration.column * self.world.tile_size,
                                decoration.baseline * self.world.tile_size - sprite.get_height()))
        return layer

    def _build_goal(self):
        pygame = self._pygame()
        layer = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        goal = pygame.Rect(self.world.goal.x, self.world.goal.y,
                           self.world.goal.width, self.world.goal.height)
        pole_x = goal.left + min(24, max(8, goal.width // 8))
        pole_top = goal.top + 8
        pole_bottom = goal.bottom - 7
        pygame.draw.line(layer, (70, 40, 40, 220),
                         (pole_x + 2, pole_top + 2),
                         (pole_x + 2, pole_bottom + 2), 4)
        pygame.draw.line(layer, (255, 247, 171, 255),
                         (pole_x, pole_top), (pole_x, pole_bottom), 3)
        flag_width = min(34, max(16, goal.width // 6))
        pygame.draw.polygon(layer, (255, 218, 82, 240), [
            (pole_x + 2, pole_top + 2),
            (pole_x + flag_width, pole_top + 9),
            (pole_x + 2, pole_top + 17),
        ])
        pygame.draw.circle(layer, (255, 218, 82, 130),
                           (pole_x, pole_bottom), 8)
        return layer

    def _build_static_scene(self):
        scene = self._build_background()
        # Decor is a background accent; terrain and gameplay markers stay on top.
        scene.blit(self._build_decorations(), (0, 0))
        scene.blit(self._build_terrain(), (0, 0))
        scene.blit(self._build_hazards(), (0, 0))
        scene.blit(self._build_goal(), (0, 0))
        return scene

    def _view(self, world_or_state, state=None) -> DisplayState:
        if state is None:
            candidate = world_or_state
        else:
            if world_or_state is not self.world:
                raise ValueError("ScreenRenderer is bound to a different world")
            candidate = state
        if isinstance(candidate, DisplayState):
            return DisplayState.from_state(candidate, candidate.session_id, self.world)
        if isinstance(candidate, Mapping):
            session_id = candidate.get("session_id")
            if not isinstance(session_id, str):
                raise ValueError("STATE session_id is required")
            return DisplayState.from_payload(candidate, session_id, self.world)
        session_id = getattr(candidate, "session_id", None)
        if not isinstance(session_id, str):
            raise ValueError("state session_id is required")
        return DisplayState.from_state(candidate, session_id, self.world)

    def present(self, world_or_state, state=None):
        if self._closed or self.screen is None:
            raise RuntimeError("screen renderer is closed")
        view = self._view(world_or_state, state)
        pygame = self._pygame()
        if self.static_scene is None:
            raise RuntimeError("static scene is not initialized")
        self.screen.blit(self.static_scene, (0, 0))
        rect = pygame.Rect(round(view.avatar.x), round(view.avatar.y),
                           self.world.spawn.width, self.world.spawn.height)
        color = (42, 145, 224) if view.avatar.alive else (190, 54, 64)
        pygame.draw.rect(self.screen, (17, 36, 57), rect)
        inner = rect.inflate(-8, -8)
        pygame.draw.rect(self.screen, color, inner)
        visor = pygame.Rect(inner.left + 10, inner.top + 11,
                           max(8, inner.width - 20), max(5, inner.height // 5))
        pygame.draw.rect(self.screen, (205, 241, 247), visor)
        pygame.draw.rect(self.screen, (17, 36, 57), rect, 3)
        self._draw_terminal_overlay(view.terminal)
        if self.owns_display:
            pygame.display.flip()
        return self.screen

    def _draw_terminal_overlay(self, terminal: str | None) -> None:
        label = terminal_label(terminal)
        if label is None:
            return
        pygame = self._pygame()
        assert self.screen is not None
        if not pygame.font.get_init():
            pygame.font.init()
        if self._terminal_fonts is None:
            self._terminal_fonts = (pygame.font.Font(None, 74),)
        title_font = self._terminal_fonts[0]
        panel_width = max(1, min(self.width - 16, 640))
        panel_height = max(1, min(self.height - 16, 190))
        panel = pygame.Surface((panel_width, panel_height), pygame.SRCALPHA)
        panel.fill((8, 18, 30, 214))
        pygame.draw.rect(panel, (224, 235, 238, 225), panel.get_rect(), 2)
        title = title_font.render(label, True, (255, 247, 205))
        panel.blit(title, title.get_rect(center=(panel_width // 2, panel_height // 2 - 24)))
        self.screen.blit(panel, panel.get_rect(center=(self.width // 2, self.height // 2)))

    render = present

    def poll_close(self) -> bool:
        if self._closed:
            return True
        if not self.owns_display:
            return False
        pygame = self._pygame()
        return any(event.type == pygame.QUIT or
                   (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE)
                   for event in pygame.event.get())

    def pace(self) -> None:
        if self.owns_display and self.clock is not None and not self._closed:
            self.clock.tick(self.fps)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.owns_display and self.pygame is not None:
            self.pygame.display.quit()
        self.screen = None
        self.static_scene = None


__all__ = ["ASSET_DIR", "DECORATION_ATLAS", "ScreenRenderer", "terminal_label"]
