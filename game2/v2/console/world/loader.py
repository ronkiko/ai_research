"""Strict loader for the V2 tile-map format."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .model import CollisionRect, Decoration, Rect, TileID, WorldDefinition

TILE_SIZE = 64
DECORATION_SPRITES = {"tree", "bush", "ruin"}
TILE_IDS = {".": TileID.EMPTY, "#": TileID.SOLID, "^": TileID.HAZARD}


def _required(data: Any, names: tuple[str, ...]) -> None:
    if not isinstance(data, dict) or set(data) != set(names):
        raise ValueError(f"object fields must be exactly {sorted(names)}")


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def _tile_rect(data: Any, tile_size: int) -> Rect:
    _required(data, ("column", "row", "columns", "rows"))
    return Rect(_integer(data["column"], "column", 0, 63) * tile_size,
                _integer(data["row"], "row", 0, 63) * tile_size,
                _integer(data["columns"], "columns", 1, 64) * tile_size,
                _integer(data["rows"], "rows", 1, 64) * tile_size)


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


def _collision_geometry(terrain: tuple[str, ...], columns: int,
                        tile_size: int, width: int,
                        height: int) -> tuple[CollisionRect, ...]:
    """Merge equal horizontal runs while retaining tile source semantics."""
    collision_rects = []
    for row_number, row in enumerate(terrain):
        column = 0
        while column < columns:
            end = column + 1
            while end < columns and row[end] == row[column]:
                end += 1
            if row[column] != ".":
                collision_rects.append(
                    CollisionRect(column * tile_size, row_number * tile_size,
                                  (end - column) * tile_size, tile_size,
                                  row[column] == "^"))
            column = end

    # The arena boundary is one tile thick and sits outside the authored grid.
    collision_rects.extend((
        CollisionRect(-tile_size, -tile_size, tile_size, height + tile_size),
        CollisionRect(width, -tile_size, tile_size, height + tile_size),
        CollisionRect(0, -tile_size, width, tile_size),
    ))
    return tuple(collision_rects)


def load_world(path: str | Path) -> WorldDefinition:
    data = _read_json(Path(path))
    required = ("schema_version", "name", "tile_size", "columns", "rows",
                "spawn", "terrain", "goal", "decorations")
    if set(data) != set(required):
        raise ValueError(f"map fields must be exactly {sorted(required)}")

    _integer(data["schema_version"], "schema_version", 2, 2)
    tile_size = _integer(data["tile_size"], "tile_size", TILE_SIZE, TILE_SIZE)
    if not isinstance(data["name"], str) or not 1 <= len(data["name"]) <= 100:
        raise ValueError("Map name must contain 1..100 characters")
    columns = _integer(data["columns"], "columns", 2, 64)
    rows = _integer(data["rows"], "rows", 2, 64)
    width, height = columns * tile_size, rows * tile_size
    spawn, goal = (_tile_rect(data["spawn"], tile_size),
                   _tile_rect(data["goal"], tile_size))
    extent = Rect(0, 0, width, height)
    if not extent.contains(spawn) or not extent.contains(goal):
        raise ValueError("Spawn and goal must fit inside the map")
    if goal.width < spawn.width or goal.height < spawn.height:
        raise ValueError("Goal must fit the entire avatar")

    terrain = data["terrain"]
    if (not isinstance(terrain, list) or len(terrain) != rows
            or any(not isinstance(row, str) or len(row) != columns
                   or set(row) - set(TILE_IDS) for row in terrain)):
        raise ValueError("terrain requires rows of . empty, # solid, ^ hazard")
    terrain_tuple = tuple(terrain)
    tiles = tuple(tuple(TILE_IDS[symbol] for symbol in row)
                  for row in terrain_tuple)

    decorations_data = data["decorations"]
    if not isinstance(decorations_data, list) or len(decorations_data) > 256:
        raise ValueError("decorations must be a list of at most 256 entries")
    decorations = []
    for item in decorations_data:
        _required(item, ("sprite", "column", "baseline"))
        if not isinstance(item["sprite"], str) or item["sprite"] not in DECORATION_SPRITES:
            raise ValueError("Unknown decoration sprite")
        decorations.append(
            Decoration(item["sprite"],
                       _integer(item["column"], "decoration column", 0, columns - 1),
                       _integer(item["baseline"], "decoration baseline", 0, rows)))

    return WorldDefinition(
        map_id=data["name"],
        name=data["name"],
        tile_size=tile_size,
        columns=columns,
        rows=rows,
        width=width,
        height=height,
        tiles=tiles,
        spawn=spawn,
        goal=goal,
        collision_rects=_collision_geometry(terrain_tuple, columns, tile_size,
                                             width, height),
        decorations=tuple(decorations),
    )
