# Tile Semantics

World authoring is semantic, not visual.

| Symbol | Stable ID | Meaning |
|---|---:|---|
| `.` | `EMPTY = 0` | Empty space; no collision |
| `#` | `SOLID = 1` | Non-damaging static collision |
| `^` | `HAZARD = 2` | Damaging static collision |

The tile ID describes what exists in the world, not how it should look. A
`SOLID` tile can later be drawn as grass, soil, brick, or stone by a human
renderer. A `HAZARD` tile can receive a different artwork without changing its
collision or death semantics.

The presentation split is:

```text
same WorldDefinition and WorldState
                 |
          +------+------+
          |             |
       screen         vision
      human-facing   model-facing
      artwork        semantic representation
```

`screen` uses tilesets, sprites, backgrounds, and presentation-owned autotiling.
`vision` encodes semantic tiles and dynamic world entities in a deterministic
world-resolution raster. The future Player transport/wire protocol is separate
and intentionally not defined here. Vision is a representation of what exists in
the game world, not privileged raw Engine telemetry such as x/y/vx/vy.

World never stores visual atlas IDs such as `GRASS_TOP` or `TILESET_INDEX_41`.
Changing screen artwork cannot change semantic terrain or collision geometry.
