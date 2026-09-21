"""Public Player-facing multi-scale logical Vision contract."""
from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Any

from .framing import PROTOCOL_VERSION, ProtocolError, encode_frame, recv_exact, recv_frame


VISION_TYPE = "vision_grid"
# Current public exteroceptive camera cadence. Motor reflexes are deliberately
# faster and may reuse the latest captured frame with newer Proprioception.
VISION_CAPTURE_HZ = 30
VISION_MAX_COLUMNS = 64
VISION_MAX_ROWS = 64
VISION_MAX_CELLS = VISION_MAX_COLUMNS * VISION_MAX_ROWS
VISION_SUBDIVISIONS = 8
VISION_FIELDS = frozenset({
    "version", "type", "session_id", "world_tick", "columns", "rows",
    "tile_size", "subdivisions", "coarse_physics_length",
    "physics_length", "metadata_length",
})

PHYSICS_EMPTY = 0
PHYSICS_SOLID = 1
PHYSICS_HAZARD = 2
_ALLOWED_PHYSICS = bytes((PHYSICS_EMPTY, PHYSICS_SOLID, PHYSICS_HAZARD))

META_GOAL = 0x01
META_SELF = 0x02
META_OTHER_ACTOR = 0x04
META_SELF_CENTER = 0x08
META_OTHER_CENTER = 0x10
META_MASK = (
    META_GOAL | META_SELF | META_OTHER_ACTOR | META_SELF_CENTER | META_OTHER_CENTER
)
_ALLOWED_METADATA = bytes(value for value in range(256) if not value & ~META_MASK)


@dataclass(frozen=True)
class VisionGrid:
    """Global coarse map plus aligned fine physics and dynamic metadata."""

    columns: int
    rows: int
    tile_size: int
    coarse_physics: bytes
    physics: bytes
    metadata: bytes
    world_tick: int
    subdivisions: int = VISION_SUBDIVISIONS

    def __post_init__(self) -> None:
        if type(self.columns) is not int or not 1 <= self.columns <= VISION_MAX_COLUMNS:
            raise ProtocolError("VisionGrid columns are outside the authored world limit")
        if type(self.rows) is not int or not 1 <= self.rows <= VISION_MAX_ROWS:
            raise ProtocolError("VisionGrid rows are outside the authored world limit")
        if self.columns * self.rows > VISION_MAX_CELLS:
            raise ProtocolError("VisionGrid is too large")
        if type(self.tile_size) is not int or self.tile_size <= 0:
            raise ProtocolError("VisionGrid tile_size must be a positive integer")
        if type(self.subdivisions) is not int or self.subdivisions != VISION_SUBDIVISIONS:
            raise ProtocolError(
                f"VisionGrid subdivisions must be {VISION_SUBDIVISIONS}"
            )
        if type(self.world_tick) is not int or self.world_tick < 0:
            raise ProtocolError("VisionGrid world_tick must be non-negative")
        for name, value in (
            ("coarse_physics", self.coarse_physics),
            ("physics", self.physics),
            ("metadata", self.metadata),
        ):
            if not isinstance(value, (bytes, bytearray)):
                raise ProtocolError(f"VisionGrid {name} must be bytes")

        coarse_physics = bytes(self.coarse_physics)
        physics = bytes(self.physics)
        metadata = bytes(self.metadata)
        coarse_cells = self.columns * self.rows
        fine_cells = self.fine_columns * self.fine_rows
        if len(coarse_physics) != coarse_cells:
            raise ProtocolError(
                "VisionGrid coarse physics length does not match tile dimensions"
            )
        if len(physics) != fine_cells:
            raise ProtocolError(
                "VisionGrid physics length does not match fine sensor dimensions"
            )
        if len(metadata) != fine_cells:
            raise ProtocolError(
                "VisionGrid metadata length does not match fine sensor dimensions"
            )
        if coarse_physics and coarse_physics.translate(None, _ALLOWED_PHYSICS):
            raise ProtocolError("VisionGrid contains an unknown coarse physics value")
        if physics and physics.translate(None, _ALLOWED_PHYSICS):
            raise ProtocolError("VisionGrid contains an unknown fine physics value")
        if metadata.translate(None, _ALLOWED_METADATA):
            raise ProtocolError("VisionGrid contains an unknown metadata bit")
        object.__setattr__(self, "coarse_physics", coarse_physics)
        object.__setattr__(self, "physics", physics)
        object.__setattr__(self, "metadata", metadata)

    @property
    def fine_columns(self) -> int:
        return self.columns * self.subdivisions

    @property
    def fine_rows(self) -> int:
        return self.rows * self.subdivisions

    @property
    def metadata_columns(self) -> int:
        return self.fine_columns

    @property
    def metadata_rows(self) -> int:
        return self.fine_rows

    @property
    def physics_columns(self) -> int:
        return self.fine_columns

    @property
    def physics_rows(self) -> int:
        return self.fine_rows

    @property
    def sensor_cell_size(self) -> float:
        return self.tile_size / self.subdivisions


def _session_id(value: Any) -> str:
    if type(value) is not str or not value:
        raise ProtocolError("Vision session_id must be a non-empty string")
    return value


def _validate_header(
    header: dict[str, Any], expected_session_id: str
) -> tuple[int, int, int, int, int]:
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
    subdivisions = header.get("subdivisions")
    if type(world_tick) is not int or world_tick < 0:
        raise ProtocolError("Vision world_tick must be non-negative")
    if (
        type(columns) is not int or not 1 <= columns <= VISION_MAX_COLUMNS
        or type(rows) is not int or not 1 <= rows <= VISION_MAX_ROWS
    ):
        raise ProtocolError("Vision grid dimensions are outside the authored world limit")
    coarse_cells = columns * rows
    if coarse_cells > VISION_MAX_CELLS:
        raise ProtocolError("Vision grid is too large")
    if type(tile_size) is not int or tile_size <= 0:
        raise ProtocolError("Vision tile_size must be a positive integer")
    if subdivisions != VISION_SUBDIVISIONS:
        raise ProtocolError(f"Vision subdivisions must be {VISION_SUBDIVISIONS}")
    fine_cells = coarse_cells * VISION_SUBDIVISIONS * VISION_SUBDIVISIONS
    if header.get("coarse_physics_length") != coarse_cells:
        raise ProtocolError("Vision coarse physics length does not match tile dimensions")
    if header.get("physics_length") != fine_cells:
        raise ProtocolError("Vision physics length does not match fine dimensions")
    if header.get("metadata_length") != fine_cells:
        raise ProtocolError("Vision metadata length does not match fine dimensions")
    return columns, rows, tile_size, subdivisions, world_tick


def send_vision_grid(sock: socket.socket, session_id: str, grid: VisionGrid) -> None:
    """Send coarse map, fine physics, and fine metadata after one strict header."""
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
        "subdivisions": grid.subdivisions,
        "coarse_physics_length": len(grid.coarse_physics),
        "physics_length": len(grid.physics),
        "metadata_length": len(grid.metadata),
    }
    sock.sendall(encode_frame(header))
    sock.sendall(grid.coarse_physics)
    sock.sendall(grid.physics)
    sock.sendall(grid.metadata)


def recv_vision_grid(sock: socket.socket, expected_session_id: str) -> VisionGrid:
    """Receive and validate one public multi-scale Vision grid."""
    header = recv_frame(sock)
    columns, rows, tile_size, subdivisions, world_tick = _validate_header(
        header, expected_session_id
    )
    coarse_physics = recv_exact(sock, header["coarse_physics_length"])
    physics = recv_exact(sock, header["physics_length"])
    metadata = recv_exact(sock, header["metadata_length"])
    return VisionGrid(
        columns, rows, tile_size, coarse_physics, physics, metadata,
        world_tick, subdivisions,
    )


__all__ = [
    "META_GOAL", "META_MASK", "META_OTHER_ACTOR", "META_OTHER_CENTER",
    "META_SELF", "META_SELF_CENTER",
    "PHYSICS_EMPTY", "PHYSICS_HAZARD", "PHYSICS_SOLID",
    "VISION_CAPTURE_HZ", "VISION_FIELDS", "VISION_MAX_CELLS",
    "VISION_MAX_COLUMNS", "VISION_MAX_ROWS", "VISION_SUBDIVISIONS",
    "VISION_TYPE", "VisionGrid",
    "recv_vision_grid", "send_vision_grid",
]
