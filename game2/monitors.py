"""Semantic MLP raster and asset-based window; no simulation or input logic."""
from dataclasses import dataclass

from protocol import encode_features, encode_frame

PALETTE = ((255, 255, 255), (0, 0, 0), (0, 102, 255), (255, 0, 0))


@dataclass(frozen=True)
class Frame:
    width: int
    height: int
    pixels: bytes  # One palette index per pixel, row-major from top left.

    def rgb(self):
        result = bytearray(len(self.pixels) * 3)
        for channel in range(3):
            table = bytes(PALETTE[i][channel] if i < len(PALETTE) else 0 for i in range(256))
            result[channel::3] = self.pixels.translate(table)
        return bytes(result)


class ColorRenderer:
    def render(self, width, height, surfaces, player):
        pixels = bytearray(width * height)

        def fill(rect, index):
            x, y = round(rect.x), round(rect.y)
            left, top = max(0, x), max(0, y)
            right, bottom = min(width, x + round(rect.width)), min(height, y + round(rect.height))
            if right <= left or bottom <= top:
                return
            row = bytes([index]) * (right - left)
            for yy in range(top, bottom):
                pixels[yy * width + left:yy * width + right] = row

        # Match physics: hazards win if solid/hazard geometry overlaps.
        for damage in (False, True):
            for surface in surfaces:
                if surface.damage == damage:
                    fill(surface, 3 if damage else 1)
        fill(player, 2)
        return Frame(width, height, bytes(pixels))

    def close(self):
        pass


class MlpMonitor:
    def __init__(self, transport):
        self.transport = transport

    def present(self, frame, metadata):
        self.transport.publish(encode_frame(frame, **metadata))

    def close(self):
        pass


class AutoMonitor:
    """Compact MLP observation transport used only by the headless scheduler."""

    def __init__(self, transport):
        self.transport = transport

    def present(self, reading, metadata):
        metadata = {key: value for key, value in metadata.items()
                    if key not in ('hz', 'monitor_hz')}
        self.transport.publish_queued(encode_features(
            distance_to_gap=reading.features[0], grounded=reading.grounded,
            **metadata))

    def close(self):
        pass


class WindowMonitor:
    HUD_HEIGHT = 80

    def __init__(self, level, spectator=False):
        width, height = level.width, level.height
        import os
        os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')
        import pygame
        self.pygame = pygame
        self.spectator = spectator
        pygame.display.init()
        pygame.font.init()
        try:
            self.screen = pygame.display.set_mode((width, height + self.HUD_HEIGHT))
            pygame.display.set_caption('game2 — physics laboratory')
            self.font = pygame.font.Font(None, 24)
            from tile_renderer import TileRenderer
            self.renderer = TileRenderer(pygame, level)
        except BaseException:
            self.close()
            raise

    def present(self, frame, metadata, player):
        pygame = self.pygame
        self.renderer.present(self.screen, player)
        pygame.draw.rect(self.screen, (225, 225, 225), (0, frame.height, frame.width, self.HUD_HEIGHT))
        status = ('ALIVE', 'DIE', 'SUCCESS')[metadata['status']]
        controls = ('MODEL CONTROL    ESC: exit' if self.spectator
                    else 'RIGHT: run    UP: jump    R: restart    ESC: exit')
        lines = [f'{controls}    |    {status}',
                 f"Physics {metadata['hz']} Hz | Episode {metadata['episode']} | "
                 f"Tick {metadata['tick']} | Overrun ticks {metadata['overrun_ticks']}"]
        for index, line in enumerate(lines):
            self.screen.blit(self.font.render(line, True, PALETTE[1]), (12, frame.height + 12 + index * 28))
        pygame.display.flip()

    def close(self):
        self.pygame.display.quit()
        self.pygame.font.quit()

    def poll_close(self):
        """Spectator window only: keyboard input cannot take over the MLP."""
        pygame = self.pygame
        return any(event.type == pygame.QUIT or
                   (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE)
                   for event in pygame.event.get())
