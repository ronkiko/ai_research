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

The intended future presentation split is:

```text
same WorldDefinition and WorldState
                 |
          +------+------+
          |             |
       screen         vision
      human-facing   model-facing
      artwork        semantic representation
```

`screen` may use tilesets, sprites, and backgrounds. `vision` may encode
semantic tiles and dynamic world entities in a simple representation. The
eventual palette or wire protocol is intentionally not fixed here. Vision is a
representation of what exists in the game world, not privileged raw
Engine telemetry such as x/y/vx/vy.
