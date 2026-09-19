# Vision

`Display.vision` is the headless machine-facing logical sensor.

It does **not** render pixels. For each World observation it emits one
`VisionGrid` with the authored tile dimensions:

```text
columns × rows
physics[rows][columns]
metadata[rows][columns]
```

`physics` contains exactly one physical class per cell:

| Value | Meaning |
|---:|---|
| 0 | `EMPTY` |
| 1 | `SOLID` |
| 2 | `HAZARD` |

`metadata` is an independent bit mask:

| Bit | Meaning |
|---:|---|
| `0x01` | `GOAL` |
| `0x02` | `SELF` |
| `0x04` | `OTHER_ACTOR` |

Bits may coexist. A cell may therefore be `SELF | GOAL` without hiding either
fact, while its physical value remains independently available.

The public grid also carries `columns`, `rows`, `tile_size`, and
`world_tick`. It does not expose velocity, grounded state, collision
rectangles, Engine input state, reward, or telemetry.

Actor metadata is marked in every tile cell intersected by the Actor AABB.
The current Actor body dimensions are the authored spawn dimensions. The Goal
is marked the same way from its authored rectangle.
