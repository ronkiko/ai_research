"""Independent V2 parser for the existing tile-map JSON format."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .physics import AvatarBody, Surface

TILE_SIZE = 64
DECORATION_SPRITES = {"tree", "bush", "ruin"}


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    def contains(self, body: Any) -> bool:
        return (self.x <= body.x and self.y <= body.y
                and body.x + body.width <= self.x + self.width
                and body.y + body.height <= self.y + self.height)


@dataclass(frozen=True)
class Decoration:
    sprite: str
    column: int
    baseline: int


@dataclass(frozen=True)
class MapData:
    map_id: str
    width: int
    height: int
    spawn: Rect
    surfaces: tuple[Surface, ...]
    goal: Rect
    terrain: tuple[str, ...]
    decorations: tuple[Decoration, ...]
    tile_size: int = TILE_SIZE

    def new_avatar(self) -> AvatarBody:
        return AvatarBody(self.spawn.x, self.spawn.y, self.spawn.width, self.spawn.height)

    def completed(self, avatar: AvatarBody) -> bool:
        return avatar.alive and avatar.grounded and self.goal.contains(avatar)


def _required(data: Any, names: tuple[str, ...]) -> None:
    if not isinstance(data, dict) or set(data) != set(names):
        raise ValueError(f"object fields must be exactly {sorted(names)}")


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def _tile_rect(data: Any) -> Rect:
    _required(data, ("column", "row", "columns", "rows"))
    return Rect(_integer(data["column"], "column", 0, 63) * TILE_SIZE,
                _integer(data["row"], "row", 0, 63) * TILE_SIZE,
                _integer(data["columns"], "columns", 1, 64) * TILE_SIZE,
                _integer(data["rows"], "rows", 1, 64) * TILE_SIZE)


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as source:
        text = source.read(1_000_001)
    if len(text) > 1_000_000:
        raise ValueError("Map exceeds 1 MB")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate field: {key}")
            result[key] = value
        return result

    def invalid_number(value):
        raise ValueError(f"Invalid JSON number: {value}")

    try:
        data = json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_number)
    except json.JSONDecodeError as exc:
        raise ValueError("Malformed map JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("Map must be a JSON object")
    return data


def load_map(path: str | Path) -> MapData:
    data = _read_json(Path(path))
    required = ("schema_version", "name", "tile_size", "columns", "rows",
                "spawn", "terrain", "goal", "decorations")
    if set(data) != set(required):
        raise ValueError(f"map fields must be exactly {sorted(required)}")
    _integer(data["schema_version"], "schema_version", 2, 2)
    _integer(data["tile_size"], "tile_size", TILE_SIZE, TILE_SIZE)
    if not isinstance(data["name"], str) or not 1 <= len(data["name"]) <= 100:
        raise ValueError("Map name must contain 1..100 characters")
    columns = _integer(data["columns"], "columns", 2, 64)
    rows = _integer(data["rows"], "rows", 2, 64)
    width, height = columns * TILE_SIZE, rows * TILE_SIZE
    spawn, goal = _tile_rect(data["spawn"]), _tile_rect(data["goal"])
    extent = Rect(0, 0, width, height)
    if not extent.contains(spawn) or not extent.contains(goal):
        raise ValueError("Spawn and goal must fit inside the map")
    if goal.width < spawn.width or goal.height < spawn.height:
        raise ValueError("Goal must fit the entire avatar")
    terrain = data["terrain"]
    if (not isinstance(terrain, list) or len(terrain) != rows
            or any(not isinstance(row, str) or len(row) != columns
                   or set(row) - set(".#^") for row in terrain)):
        raise ValueError("terrain requires rows of . empty, # solid, ^ hazard")
    surfaces = []
    for row_number, row in enumerate(terrain):
        column = 0
        while column < columns:
            end = column + 1
            while end < columns and row[end] == row[column]:
                end += 1
            if row[column] != ".":
                surfaces.append(Surface(column * TILE_SIZE, row_number * TILE_SIZE,
                                        (end - column) * TILE_SIZE, TILE_SIZE,
                                        row[column] == "^"))
            column = end
    surfaces.extend((Surface(-TILE_SIZE, -TILE_SIZE, TILE_SIZE, height + TILE_SIZE),
                     Surface(width, -TILE_SIZE, TILE_SIZE, height + TILE_SIZE),
                     Surface(0, -TILE_SIZE, width, TILE_SIZE)))
    if not isinstance(data["decorations"], list) or len(data["decorations"]) > 256:
        raise ValueError("decorations must be a list of at most 256 entries")
    decorations = []
    for item in data["decorations"]:
        _required(item, ("sprite", "column", "baseline"))
        if not isinstance(item["sprite"], str) or item["sprite"] not in DECORATION_SPRITES:
            raise ValueError("Unknown decoration sprite")
        decorations.append(Decoration(item["sprite"],
                                       _integer(item["column"], "decoration column", 0, columns - 1),
                                       _integer(item["baseline"], "decoration baseline", 0, rows)))
    return MapData(data["name"], width, height, spawn, tuple(surfaces), goal,
                   tuple(terrain), tuple(decorations))
