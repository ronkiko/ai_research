"""Validated, immutable level data loaded from JSON; no rendering or game loop."""
from dataclasses import dataclass
from pathlib import Path

from controls import fields, strict_json
from physics import Body, Surface

DEFAULT_MAP = Path(__file__).with_name('maps') / 'pit.json'


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
class Level:
    name: str
    width: int
    height: int
    spawn: Rect
    surfaces: tuple
    goal: Rect

    def new_body(self):
        return Body(self.spawn.x, self.spawn.y, self.spawn.width, self.spawn.height)

    def completed(self, body):
        return body.alive and body.grounded and self.goal.contains(body)


def integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{name} must be an integer in [{minimum}, {maximum}]')
    return value


def rectangle(data):
    fields(data, ('x', 'y', 'width', 'height'))
    return Rect(integer(data['x'], 'x', -8192, 8192),
                integer(data['y'], 'y', -8192, 8192),
                integer(data['width'], 'width', 1, 8192),
                integer(data['height'], 'height', 1, 8192))


def load_level(path):
    with Path(path).open(encoding='utf-8') as source:
        text = source.read(1_000_001)
    if len(text) > 1_000_000:
        raise ValueError('Map exceeds 1 MB')
    data = strict_json(text)
    fields(data, ('schema_version', 'name', 'width', 'height', 'spawn', 'surfaces', 'goal'))
    integer(data['schema_version'], 'schema_version', 1, 1)
    if not isinstance(data['name'], str) or not 1 <= len(data['name']) <= 100:
        raise ValueError('Map name must contain 1..100 characters')
    width = integer(data['width'], 'world width', 64, 4096)
    height = integer(data['height'], 'world height', 64, 4096)
    spawn, goal = rectangle(data['spawn']), rectangle(data['goal'])
    extent = Rect(0, 0, width, height)
    if not extent.contains(spawn) or not extent.contains(goal):
        raise ValueError('Spawn and goal must fit inside the image')
    if goal.width < spawn.width or goal.height < spawn.height:
        raise ValueError('Goal must fit the entire player')
    if not isinstance(data['surfaces'], list) or not 1 <= len(data['surfaces']) <= 512:
        raise ValueError('Map requires 1..512 surfaces')
    surfaces = []
    for item in data['surfaces']:
        fields(item, ('x', 'y', 'width', 'height', 'damage'))
        if type(item['damage']) is not bool:
            raise ValueError('damage must be a boolean')
        rect = rectangle({k: item[k] for k in ('x', 'y', 'width', 'height')})
        surfaces.append(Surface(rect.x, rect.y, rect.width, rect.height, item['damage']))
    return Level(data['name'], width, height, spawn, tuple(surfaces), goal)
