# Screen Versus Vision

| Interface | Consumer | Representation |
|---|---|---|
| `screen` | human observer | rendered RGB frame |
| `vision` | machine Player | deterministic logical `VisionGrid` |

Changing artwork, tilesets, autotiling, HUD, or Screen resolution cannot change
the machine observation. Vision is built from stable World tile semantics and
public-safe Actor occupancy.

The two Vision matrices have the authored map size. For a 20×12 training map
the sensor carries 240 physics bytes and 240 metadata bytes, rather than a
1280×768 semantic image.
