"""Asset-based presentation only; no collision or sensor logic."""
from pathlib import Path

from level import DECOR_SPRITES, SOURCE_TILE_SIZE

ASSETS = Path(__file__).with_name('assets') / 'FreeCuteTileset'


class TileRenderer:
    def __init__(self, pygame, level):
        self.pygame, self.level = pygame, level
        size = (level.width, level.height)
        self.background = pygame.Surface(size).convert()
        self.background.fill((80, 130, 200))
        self.ground = pygame.Surface(size, pygame.SRCALPHA)
        self.decorations = pygame.Surface(size, pygame.SRCALPHA)
        scale = level.tile_size // SOURCE_TILE_SIZE
        # Original background composition from the pack: sky, mountains, hills.
        # Integer nearest-neighbor scale preserves pixel-art edges.
        for name in ('BG1.png', 'BG2.png', 'BG3.png'):
            source = pygame.image.load(str(ASSETS / name)).convert_alpha()
            image = pygame.transform.scale(source, (source.get_width()*scale, source.get_height()*scale))
            for x in range(0, level.width, image.get_width()):
                self.background.blit(image, (x, level.height - image.get_height()))
        atlas = pygame.image.load(str(ASSETS / 'Tileset.png')).convert_alpha()
        self.tiles = {}
        for y in (0, 1):
            for x in (0, 1, 2):
                source = atlas.subsurface((x*16, y*16, 16, 16))
                self.tiles[x, y] = pygame.transform.scale(source, (level.tile_size, level.tile_size))
        for row, cells in enumerate(level.terrain):
            for column, cell in enumerate(cells):
                if cell == '.':
                    continue
                rect = pygame.Rect(column*level.tile_size, row*level.tile_size,
                                   level.tile_size, level.tile_size)
                if cell == '#':
                    top = row == 0 or level.terrain[row-1][column] != '#'
                    left = column > 0 and cells[column-1] != '#'
                    right = column+1 < len(cells) and cells[column+1] != '#'
                    x = 0 if left else 2 if right else 1
                    # Backing fills tiny transparent atlas corners inside the collider.
                    self.ground.fill((53, 29, 40), rect)
                    self.ground.blit(self.tiles[x, 0 if top else 1], rect)
                else:
                    self.ground.fill((69, 33, 47), rect)
                    if row == 0 or level.terrain[row-1][column] != '^':
                        # Pack has no hazard tile: draw a tile-aligned spike strip.
                        for offset in range(0, level.tile_size, 16):
                            x, y = rect.x + offset, rect.y
                            pygame.draw.polygon(self.ground, (228, 221, 204),
                                                [(x, y+24), (x+8, y), (x+16, y+24)])
                            pygame.draw.line(self.ground, (185, 58, 52), (x+8, y), (x+12, y+12), 3)
        atlas = pygame.image.load(str(ASSETS / 'Decors.png')).convert_alpha()
        sprites = {name: pygame.transform.scale(atlas.subsurface(rect), (rect[2]*scale, rect[3]*scale))
                   for name, rect in DECOR_SPRITES.items()}
        for decoration in level.decorations:
            sprite = sprites[decoration.sprite]
            self.decorations.blit(sprite, (decoration.column*level.tile_size,
                                           decoration.baseline*level.tile_size - sprite.get_height()))

    def present(self, screen, player):
        screen.blit(self.background, (0, 0))
        screen.blit(self.ground, (0, 0))
        screen.blit(self.decorations, (0, 0))
        rect = self.pygame.Rect(round(player.x), round(player.y), player.width, player.height)
        # Keep the controllable square legible over scenery; it is not a decor tile.
        self.pygame.draw.rect(screen, (34, 126, 232), rect)
        self.pygame.draw.rect(screen, (197, 236, 255), rect, 3)
