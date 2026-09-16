"""Immutable, tile-authored definitions of Console game worlds."""

from .loader import load_world
from .model import (CollisionRect, Decoration, EMPTY, HAZARD, SOLID, Rect,
                    SemanticTile, TileID, WorldDefinition)

__all__ = [
    "CollisionRect",
    "Decoration",
    "EMPTY",
    "HAZARD",
    "Rect",
    "SemanticTile",
    "SOLID",
    "TileID",
    "WorldDefinition",
    "load_world",
]
