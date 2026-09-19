"""Public Player-facing logical Vision grid contract."""
from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Any

from .framing import PROTOCOL_VERSION, ProtocolError, encode_frame, recv_exact, recv_frame


VISION_TYPE = "vision_grid"
VISION_MAX_CELLS = 64 * 64
VISION_FIELDS = frozenset({
    "version", "type", "session_id", "world_tick", "columns", "rows",
    "tile_size", "physics_length", "metadata_length",
})

PHYSICS_EMPTY = 0
PHYSICS_SOLID = 1
PHYSICS_HAZARD = 2
_ALLOWED_PHYSICS = bytes((PHYSICS_EMPTY, PHYSICS_SOLID, PHYSICS_HAZARD))

META_GOAL = 0x01
META_SELF = 0x02
META_OTHER_ACTOR = 0x04
META_MASK = META_GOAL | META_SELF | META_OTHER_ACTOR


@dataclass(frozen=True)
class VisionGrid:
    """One logical tile-grid observation exposed by the public Vision peripheral."""

    columns: int
    rows: int
    tile_size: int
    physics: bytes
    metadata: bytes
    world_tick: int

    def __post_init__(self) -> None:
        if type(self.columns) is not int or self.columns <= 0:
            raise ProtocolError("VisionGrid columns must be a positive integer")
        if type(self.rows) is not int or self.rows <= 0:
            raise ProtocolError("VisionGrid rows must be a positive integer")
        if self.columns * self.rows > VISION_MAX_CELLS:
            raise ProtocolError("VisionGrid is too large")
        if type(self.tile_size) is not int or self.tile_size <= 0:
            raise ProtocolError("VisionGrid tile_size must be a positive integer")
        if type(self.world_tick) is not int or self.world_tick < 0:
            raise ProtocolError("VisionGrid world_tick must be non-negative")
        if not isinstance(self.physics, (bytes, bytearray)):
            raise ProtocolError("VisionGrid physics must be bytes")
        if not isinstance(self.metadata, (bytes, bytearray)):
            raise ProtocolError("VisionGrid metadata must be bytes")
        physics = bytes(self.physics)
        metadata = bytes(self.metadata)
        cells = self.columns * self.rows
        if len(physics) != cells or len(metadata) != cells:
            raise ProtocolError("VisionGrid matrix lengths do not match dimensions")
        if physics and physics.translate(None, _ALLOWED_PHYSICS):
            raise ProtocolError("VisionGrid contains an unknown physics value")
        if any(value & ~META_MASK for value in metadata):
            raise ProtocolError("VisionGrid contains an unknown metadata bit")
        object.__setattr__(self, "physics", physics)
        object.__setattr__(self, "metadata", metadata)


def _session_id(value: Any) -> str:
    if type(value) is not str or not value:
        raise ProtocolError("Vision session_id must be a non-empty string")
    return value


def _validate_header(
    header: dict[str, Any], expected_session_id: str
) -> tuple[int, int, int, int]:
    if not isinstance(header, dict) or set(header) != VISION_FIELDS:
        raise ProtocolError("Vision header fields are invalid")
    if header.get("version") != PROTOCOL_VERSION:
        raise ProtocolError("unsupported Vision protocol version")
    if header.get("type") != VISION_TYPE:
        raise ProtocolError("invalid Vision grid type")
    if _session_id(header.get("session_id")) != _session_id(expected_session_id):
        raise ProtocolError("Vision session_id does not match expected session")
    world_tick = header.get("world_tick")
    columns = header.get("columns")
    rows = header.get("rows")
    tile_size = header.get("tile_size")
    if type(world_tick) is not int or world_tick < 0:
        raise ProtocolError("Vision world_tick must be non-negative")
    if type(columns) is not int or columns <= 0 or type(rows) is not int or rows <= 0:
        raise ProtocolError("Vision grid dimensions must be positive integers")
    cells = columns * rows
    if cells > VISION_MAX_CELLS:
        raise ProtocolError("Vision grid is too large")
    if type(tile_size) is not int or tile_size <= 0:
        raise ProtocolError("Vision tile_size must be a positive integer")
    if header.get("physics_length") != cells or header.get("metadata_length") != cells:
        raise ProtocolError("Vision matrix lengths do not match dimensions")
    return columns, rows, tile_size, world_tick


def send_vision_grid(sock: socket.socket, session_id: str, grid: VisionGrid) -> None:
    """Send a strict JSON header followed by physics and metadata matrices."""
    if not isinstance(grid, VisionGrid):
        raise TypeError("send_vision_grid requires a VisionGrid")
    session_id = _session_id(session_id)
    header = {
        "version": PROTOCOL_VERSION,
        "type": VISION_TYPE,
        "session_id": session_id,
        "world_tick": grid.world_tick,
        "columns": grid.columns,
        "rows": grid.rows,
        "tile_size": grid.tile_size,
        "physics_length": len(grid.physics),
        "metadata_length": len(grid.metadata),
    }
    sock.sendall(encode_frame(header))
    sock.sendall(grid.physics)
    sock.sendall(grid.metadata)


def recv_vision_grid(sock: socket.socket, expected_session_id: str) -> VisionGrid:
    """Receive and validate one public logical Vision grid."""
    header = recv_frame(sock)
    columns, rows, tile_size, world_tick = _validate_header(
        header, expected_session_id
    )
    physics = recv_exact(sock, header["physics_length"])
    metadata = recv_exact(sock, header["metadata_length"])
    return VisionGrid(columns, rows, tile_size, physics, metadata, world_tick)


__all__ = [
    "META_GOAL", "META_MASK", "META_OTHER_ACTOR", "META_SELF",
    "PHYSICS_EMPTY", "PHYSICS_HAZARD", "PHYSICS_SOLID",
    "VISION_FIELDS", "VISION_MAX_CELLS", "VISION_TYPE", "VisionGrid",
    "recv_vision_grid", "send_vision_grid",
]
