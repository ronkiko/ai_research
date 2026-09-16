"""Pure semantic-neighborhood to tileset-variant mapping."""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntFlag
from typing import Sequence

from ...world import TileID


class NeighborMask(IntFlag):
    NONE = 0
    NORTH = 1
    EAST = 2
    SOUTH = 4
    WEST = 8


N = NeighborMask.NORTH
E = NeighborMask.EAST
S = NeighborMask.SOUTH
W = NeighborMask.WEST


@dataclass(frozen=True)
class TileVariant:
    """Presentation choice; atlas coordinates never enter WorldDefinition."""

    tile: TileID
    neighbor_mask: NeighborMask
    atlas_cell: tuple[int, int] | None = None

    @property
    def atlas_x(self) -> int | None:
        return self.atlas_cell[0] if self.atlas_cell is not None else None

    @property
    def atlas_y(self) -> int | None:
        return self.atlas_cell[1] if self.atlas_cell is not None else None


class AutoTiler:
    """Select the available FreeCuteTileset cell from cardinal topology."""

    def neighbor_mask(self, tiles: Sequence[Sequence[TileID]], row: int,
                      column: int) -> NeighborMask:
        tile = tiles[row][column]
        if tile is TileID.EMPTY:
            return NeighborMask.NONE
        height, width = len(tiles), len(tiles[row])
        mask = NeighborMask.NONE
        neighbors = ((-1, 0, NeighborMask.NORTH), (0, 1, NeighborMask.EAST),
                     (1, 0, NeighborMask.SOUTH), (0, -1, NeighborMask.WEST))
        for row_delta, column_delta, direction in neighbors:
            neighbor_row, neighbor_column = row + row_delta, column + column_delta
            if (0 <= neighbor_row < height and 0 <= neighbor_column < width
                    and tiles[neighbor_row][neighbor_column] is tile):
                mask |= direction
        return mask

    def variant_for(self, tiles: Sequence[Sequence[TileID]], row: int,
                    column: int) -> TileVariant:
        tile = tiles[row][column]
        mask = self.neighbor_mask(tiles, row, column)
        if tile is TileID.SOLID:
            left = bool(mask & NeighborMask.WEST)
            right = bool(mask & NeighborMask.EAST)
            top = not bool(mask & NeighborMask.NORTH)
            cell_x = 0 if not left else 2 if not right else 1
            cell_y = 0 if top else 1
            return TileVariant(tile, mask, (cell_x, cell_y))
        return TileVariant(tile, mask)


__all__ = ["AutoTiler", "E", "N", "NeighborMask", "S", "TileVariant", "W"]
