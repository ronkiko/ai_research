"""Tile-map data and conversion to colliders; the physics engine stays pixel-based."""
from dataclasses import dataclass
from pathlib import Path

from controls import fields, strict_json
from physics import Body, Surface

DEFAULT_MAP = Path(__file__).with_name('maps') / 'pit.json'
TILE_SIZE = 64
SOURCE_TILE_SIZE = 16
DECOR_SPRITES = {'tree': (16, 0, 80, 112), 'bush': (112, 96, 48, 16),
                 'ruin': (176, 80, 32, 32)}


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    def contains(self, body):
        return (self.x <= body.x and self.y <= body.y
                and body.x + body.width <= self.x + self.width
                and body.y + body.height <= self.y + self.height)


@dataclass(frozen=True)
class Decoration:
    sprite: str
    column: int
    baseline: int


@dataclass(frozen=True)
class Level:
    name: str
    width: int
    height: int
    spawn: Rect
    surfaces: tuple
    goal: Rect
    terrain: tuple[str, ...]
    decorations: tuple[Decoration, ...]
    tile_size: int = TILE_SIZE

    def new_body(self):
        return Body(self.spawn.x, self.spawn.y, self.spawn.width, self.spawn.height)

    def completed(self, body):
        return body.alive and body.grounded and self.goal.contains(body)


def integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{name} must be an integer in [{minimum}, {maximum}]')
    return value


def tile_rectangle(data):
    fields(data, ('column', 'row', 'columns', 'rows'))
    return Rect(integer(data['column'], 'column', 0, 63) * TILE_SIZE,
                integer(data['row'], 'row', 0, 63) * TILE_SIZE,
                integer(data['columns'], 'columns', 1, 64) * TILE_SIZE,
                integer(data['rows'], 'rows', 1, 64) * TILE_SIZE)


def load_level(path):
    with Path(path).open(encoding='utf-8') as source:
        text = source.read(1_000_001)
    if len(text) > 1_000_000:
        raise ValueError('Map exceeds 1 MB')
    data = strict_json(text)
    fields(data, ('schema_version', 'name', 'tile_size', 'columns', 'rows',
                  'spawn', 'terrain', 'goal', 'decorations'))
    integer(data['schema_version'], 'schema_version', 2, 2)
    integer(data['tile_size'], 'tile_size', TILE_SIZE, TILE_SIZE)
    if not isinstance(data['name'], str) or not 1 <= len(data['name']) <= 100:
        raise ValueError('Map name must contain 1..100 characters')
    columns = integer(data['columns'], 'columns', 2, 64)
    rows = integer(data['rows'], 'rows', 2, 64)
    width, height = columns * TILE_SIZE, rows * TILE_SIZE
    spawn, goal = tile_rectangle(data['spawn']), tile_rectangle(data['goal'])
    extent = Rect(0, 0, width, height)
    if not extent.contains(spawn) or not extent.contains(goal):
        raise ValueError('Spawn and goal must fit inside the image')
    if goal.width < spawn.width or goal.height < spawn.height:
        raise ValueError('Goal must fit the entire player')
    terrain = data['terrain']
    if (not isinstance(terrain, list) or len(terrain) != rows
            or any(not isinstance(row, str) or len(row) != columns
                   or set(row) - set('.#^') for row in terrain)):
        raise ValueError('terrain requires rows of columns cells: . empty, # solid, ^ damage')
    surfaces = []
    # Merge equal horizontal runs: tile-authoring precision, few physics colliders.
    for y, row in enumerate(terrain):
        x = 0
        while x < columns:
            end = x + 1
            while end < columns and row[end] == row[x]:
                end += 1
            if row[x] != '.':
                surfaces.append(Surface(x * TILE_SIZE, y * TILE_SIZE,
                                        (end - x) * TILE_SIZE, TILE_SIZE, row[x] == '^'))
            x = end
    # Arena boundary walls are one tile thick, just outside the image.
    surfaces.extend((Surface(-TILE_SIZE, -TILE_SIZE, TILE_SIZE, height + TILE_SIZE),
                     Surface(width, -TILE_SIZE, TILE_SIZE, height + TILE_SIZE),
                     Surface(0, -TILE_SIZE, width, TILE_SIZE)))
    if not isinstance(data['decorations'], list) or len(data['decorations']) > 256:
        raise ValueError('decorations must be a list of at most 256 entries')
    decorations = []
    for item in data['decorations']:
        fields(item, ('sprite', 'column', 'baseline'))
        if not isinstance(item['sprite'], str) or item['sprite'] not in DECOR_SPRITES:
            raise ValueError('Unknown decoration sprite')
        column = integer(item['column'], 'decoration column', 0, columns - 1)
        baseline = integer(item['baseline'], 'decoration baseline', 0, rows)
        decorations.append(Decoration(item['sprite'], column, baseline))
    return Level(data['name'], width, height, spawn, tuple(surfaces), goal,
                 tuple(terrain), tuple(decorations))
