"""Immutable data model for a loaded game world.

This module describes a scene but does not create an avatar, a physics object,
or any other mutable runtime state.  CollisionRect is deliberately a world
owned geometry value; Engine converts it to its physics representation.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any


class TileID(IntEnum):
    """Stable semantic tile classes, independent of visual artwork."""

    EMPTY = 0
    SOLID = 1
    HAZARD = 2


# SemanticTile is the descriptive name used by the world documentation.
SemanticTile = TileID
EMPTY = TileID.EMPTY
SOLID = TileID.SOLID
HAZARD = TileID.HAZARD


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if not all(type(value) is int for value in
                   (self.x, self.y, self.width, self.height)):
            raise ValueError("Rect geometry must use integers")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Rect dimensions must be positive")

    def contains_geometry(self, x: float, y: float, width: float,
                          height: float) -> bool:
        return (self.x <= x and self.y <= y
                and x + width <= self.x + self.width
                and y + height <= self.y + self.height)

    def contains(self, geometry: Any) -> bool:
        """Contain a rectangle-like value without depending on Engine types."""
        return self.contains_geometry(geometry.x, geometry.y, geometry.width,
                                      geometry.height)

    def intersects_geometry(self, x: float, y: float, width: float,
                            height: float) -> bool:
        """Return true only when geometry actually overlaps this rectangle."""
        return (
            x < self.x + self.width
            and x + width > self.x
            and y < self.y + self.height
            and y + height > self.y
        )


@dataclass(frozen=True)
class CollisionRect:
    """Static collision geometry derived from one or more semantic tiles."""

    x: int
    y: int
    width: int
    height: int
    damage: bool = False

    def __post_init__(self) -> None:
        if not all(type(value) is int for value in
                   (self.x, self.y, self.width, self.height)):
            raise ValueError("Collision geometry must use integers")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Collision dimensions must be positive")
        if type(self.damage) is not bool:
            raise ValueError("Collision damage must be boolean")


@dataclass(frozen=True)
class Decoration:
    sprite: str
    column: int
    baseline: int


@dataclass(frozen=True)
class WorldDefinition:
    """The complete immutable definition of a tile-authored scene."""

    map_id: str
    name: str
    tile_size: int
    columns: int
    rows: int
    width: int
    height: int
    tiles: tuple[tuple[TileID, ...], ...]
    spawn: Rect
    goal: Rect
    collision_rects: tuple[CollisionRect, ...]
    decorations: tuple[Decoration, ...]

    def __post_init__(self) -> None:
        if self.width != self.columns * self.tile_size:
            raise ValueError("World width does not match its grid")
        if self.height != self.rows * self.tile_size:
            raise ValueError("World height does not match its grid")
        if len(self.tiles) != self.rows or any(
                len(row) != self.columns for row in self.tiles):
            raise ValueError("World tile grid dimensions are invalid")
        if any(type(tile) is not TileID for row in self.tiles for tile in row):
            raise ValueError("World tile grid contains an unknown tile ID")

    @property
    def world_id(self) -> str:
        return self.map_id

    @property
    def semantic_tiles(self) -> tuple[tuple[TileID, ...], ...]:
        return self.tiles

    @property
    def terrain(self) -> tuple[str, ...]:
        symbols = {TileID.EMPTY: ".", TileID.SOLID: "#", TileID.HAZARD: "^"}
        return tuple("".join(symbols[tile] for tile in row) for row in self.tiles)

    @property
    def collision_geometry(self) -> tuple[CollisionRect, ...]:
        return self.collision_rects

    def completed(self, x: float, y: float, width: float, height: float,
                  grounded: bool, alive: bool) -> bool:
        """Win when a living Actor actually overlaps the authored goal cell.

        Grounded is deliberately not required: the goal remains a non-solid
        spatial trigger, so an Actor can touch it while airborne, or jump fully
        over it without winning.
        """
        return alive and self.goal.intersects_geometry(x, y, width, height)
