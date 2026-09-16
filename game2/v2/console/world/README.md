# World

World is the Console's immutable game-scene domain. It owns the map schema,
tile-authored semantic grid, spawn, goal, decorations metadata, and collision
geometry derived from the grid.

World does not own Avatar position or velocity, episodes, ticks, scheduled
actions, Player/model code, a renderer, or a Physics engine. It does not import
Engine runtime code. Engine creates the mutable physical instance from a
`WorldDefinition` and remains authoritative during play. This dependency
direction is enforced by the V2 architecture tests.

The source of truth is a small JSON tile grid. `.` is empty space, `#` is a
solid tile, and `^` is a hazard tile. Equal horizontal runs are merged into
static collision rectangles as a loader optimization; the semantic grid is
retained unchanged.

See:

- `doc/WORLD_MODEL.md` for the immutable object boundary;
- `doc/MAP_FORMAT.md` for the JSON schema;
- `doc/TILE_SEMANTICS.md` for stable tile meanings;
- `maps/` for V2-owned map files.
