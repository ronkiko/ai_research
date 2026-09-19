# Game2 Grid Vision

Status: current architecture

Grid Vision is the machine-facing visual representation used by Game2 V2.
It is not a screenshot and it is not a downsampled RGB image. It is a logical,
world-aligned sensor derived from the same authored map and collision geometry
used by the Console.

The public observation contains three aligned data planes:

```text
World / authored map
20 x 12 tiles
64 px per tile
        |
        +--> coarse_physics 20 x 12
        |        drafting / global map / future minimap
        |
        +--> subdivisions = 8
                 |
                 v
            160 x 96 fine grid
            8 px per fine cell
                 |
                 +--> physics  160 x 96
                 +--> metadata 160 x 96
```

For the current Level 1 maps:

```text
columns          = 20
rows             = 12
tile_size        = 64 px
subdivisions     = 8

fine_columns     = 20 * 8 = 160
fine_rows        = 12 * 8 = 96
sensor_cell_size = 64 / 8 = 8 px
```

The important rule is that the coarse grid and the fine grids have different
jobs. The coarse grid describes which authored tile contains terrain or danger.
The fine grids describe where physical geometry and dynamic entities actually
are inside that tile.

## 1. Coarse physics

`coarse_physics` has one byte per authored map tile.

Values:

```text
0 EMPTY
1 SOLID
2 HAZARD
```

Its dimensions are always:

```text
rows x columns
```

For the current map this is `12 x 20`.

The coarse plane is the semantic map used to author and understand the world at
tile scale. It intentionally does not describe the exact shape of a collider
inside a tile.

Example: if a tile contains spikes only in its lower part, the complete coarse
cell is still `HAZARD`.

```text
coarse tile:

^
```

This is desirable because the coarse plane is intended to remain useful as a
drafting representation and later as a global minimap when the world becomes
larger than one screen. A minimap needs to answer "there is danger in this map
cell", not the exact pixel where the spike begins.

## 2. Fine physics

`physics` is the precise machine-facing physical map.

It uses the same values:

```text
0 EMPTY
1 SOLID
2 HAZARD
```

but its dimensions are:

```text
fine_rows x fine_columns
```

For the current map this is `96 x 160`.

Fine physics is rasterized from the authoritative `WorldDefinition.collision_rects`,
not by blindly expanding every coarse tile to an 8 x 8 block. Therefore a
collider can occupy only part of an authored tile.

### Partial hazards

The current spike tile is the canonical example.

An authored hazard tile is 64 px high. Its actual damage collider occupies only
the lower 24 px:

```text
64 px hazard tile
+--------+
|        |
|        |
|        |
|        |
|        |
|^^^^^^^^|  8 px
|^^^^^^^^|  8 px
|^^^^^^^^|  8 px
+--------+
             24 px damage
```

At fine resolution:

```text
........
........
........
........
........
^^^^^^^^
^^^^^^^^
^^^^^^^^
```

The same `CollisionRect` is used by the Engine and by the fine Vision
renderer. Human spike artwork is also drawn from that damage geometry.

Consequently:

- entering the upper empty part of a hazard tile is not damage;
- touching the real lower damage collider can kill the Actor;
- fine Vision reports the same dangerous region that Engine Physics uses;
- coarse Vision still reports the tile as `HAZARD`.

The Vision grid never decides whether the Actor dies. Engine Physics remains
authoritative. Vision is only an observation of the physical world.

## 3. Fine metadata

`metadata` has exactly the same dimensions and cell size as fine physics.

Metadata is a bit field, so several meanings may coexist in the same fine cell:

```text
0x01 META_GOAL
0x02 META_SELF
0x04 META_OTHER_ACTOR
0x08 META_SELF_CENTER
0x10 META_OTHER_CENTER
```

Body flags are written into every fine cell intersected by the corresponding
AABB. Center flags identify the single fine cell containing the center of an
Actor.

This makes entity movement visible below tile resolution.

With a 64 px authored tile and 8 subdivisions, the machine sensor observes
motion in 8 px spatial steps instead of 64 px tile jumps.

```text
coarse observation:
tile 2 -----------------> tile 3

fine observation:
cell 16 -> 17 -> 18 -> 19 -> 20 -> ...
```

The fine grid is still a quantized sensor. It is not a leak of exact Engine
coordinates.

## 4. Coordinate relationship

The sensor has an explicit physical scale.

```text
sensor_cell_size = tile_size / subdivisions
```

Currently:

```text
sensor_cell_size = 64 / 8 = 8 px
```

Approximate world coordinates can therefore be recovered from a fine cell:

```text
world_x ~= fine_column * sensor_cell_size
world_y ~= fine_row    * sensor_cell_size
```

The center of a fine cell is:

```text
world_center_x = (fine_column + 0.5) * sensor_cell_size
world_center_y = (fine_row    + 0.5) * sensor_cell_size
```

This lets Player-side logic reason about approximate physical distances without
receiving private Engine `x`, `y`, `vx`, or `vy`.

For example, if the Actor center and the beginning of a gap differ by 19 fine
cells:

```text
distance ~= 19 * 8 px = 152 px
```

## 5. Motion and speed

Velocity is not published by Vision.

The Player observes `META_SELF_CENTER` over time and combines it with
`world_tick`. The current `MotionEstimator` keeps a short window of observed
centers and estimates horizontal movement from their displacement.

Conceptually:

```text
position(t0) = observed SELF_CENTER
position(t1) = observed SELF_CENTER

observed_velocity =
    (position(t1) - position(t0))
    / (world_tick(t1) - world_tick(t0))
```

The current default observation window is 8 world ticks. This smooths the
8 px quantization without inventing movement that was not observed.

This is deliberately different from exposing `ActorBody.vx`. The model sees
the consequences of the world's acceleration, braking, gravity and inertia
through successive observations instead of receiving private physics state.

## 6. Public VisionGrid contract

The immutable public object is:

```text
VisionGrid
    columns
    rows
    tile_size
    subdivisions
    coarse_physics
    physics
    metadata
    world_tick
```

Derived geometry:

```text
fine_columns     = columns * subdivisions
fine_rows        = rows * subdivisions
physics_columns  = fine_columns
physics_rows     = fine_rows
metadata_columns = fine_columns
metadata_rows    = fine_rows
sensor_cell_size = tile_size / subdivisions
```

Current limits:

```text
columns <= 64
rows    <= 64
subdivisions = 8
```

The public contract does not expose Engine telemetry such as:

```text
x
y
vx
vy
grounded
input_right
input_jump
collision_rects
```

## 7. Wire format

Vision uses a strict framed JSON header followed by raw byte planes.

Order:

```text
framed JSON header
coarse_physics bytes
fine physics bytes
fine metadata bytes
```

The header includes:

```text
version
type = vision_grid
session_id
world_tick
columns
rows
tile_size
subdivisions
coarse_physics_length
physics_length
metadata_length
```

For a 20 x 12 world:

```text
coarse_physics = 20 * 12          =   240 bytes
physics        = 160 * 96         = 15360 bytes
metadata       = 160 * 96         = 15360 bytes
```

The logical sensor therefore does not send RGB pixels, image compression, or a
downsampled screenshot.

## 8. Learned model representation

The learned Planner uses only the fine planes for immediate control.

Fine physics becomes three one-hot channels:

```text
EMPTY
SOLID
HAZARD
```

Fine metadata becomes five independent binary channels:

```text
SELF
GOAL
OTHER_ACTOR
SELF_CENTER
OTHER_CENTER
```

The CNN input is therefore:

```text
8 x fine_rows x fine_columns
```

For the current world:

```text
8 x 96 x 160
```

`coarse_physics` is retained in the public observation and replay record but
is not currently expanded into the Planner CNN input. Its purpose is global
map semantics rather than local fine control.

The current Planner configuration is:

```text
adaptive-spatial-fine-physics-v5
```

Older raster, coarse-only, and pre-fine-physics Planner checkpoints are not
compatible with this representation.

## 9. Replay and Model IPC

A training record preserves the exact logical observation:

```text
columns
rows
tile_size
subdivisions
world_tick

coarse_physics
physics
metadata

motion_x
action_decision
pad_right
pad_jump
```

Player-to-Model OBSERVE IPC carries the same three raw planes. This prevents
training replay from silently reconstructing a different world representation
than the one used for inference.

## 10. Human Screen versus Grid Vision

Human Screen and machine Vision are separate Display outputs:

```text
WorldDefinition + WorldState
           |
         Display
        /       \
       /         \
 Screen RGB     Grid Vision
 human          machine
```

Screen renders artwork and presentation.

Grid Vision renders logical geometry.

Neither output is authoritative for Physics. Engine owns the world and
collisions; both presentations observe it.

## 11. Future scrolling worlds

The current contract sends fine physics and metadata for the complete authored
world. This is acceptable while maps fit within the current bounds.

When the labyrinth becomes larger than one screen, the intended separation is:

```text
global coarse_physics
    entire authored labyrinth
    low resolution
    drafting / navigation / minimap

local fine physics + metadata
    current machine-visible region
    high resolution
    precise movement / collision reasoning
```

That future viewport cut must be an explicit contract change. It must preserve
the physical scale and an unambiguous relationship between local fine
coordinates and global coarse/world coordinates.

## 12. Core invariants

1. Engine Physics is authoritative; Vision never determines collisions.
2. `coarse_physics` represents authored tile semantics.
3. Fine `physics` represents actual collision geometry at 8 px resolution.
4. Fine `metadata` represents dynamic/public semantic occupancy at the same
   resolution as fine physics.
5. Physics and metadata are independent planes.
6. Actor center is explicit through `META_SELF_CENTER` /
   `META_OTHER_CENTER`.
7. Private Engine position and velocity are not published.
8. Speed is inferred from successive public observations and `world_tick`.
9. Partial colliders must appear partial in fine physics.
10. Human Screen geometry and fine Vision geometry must agree with authoritative
    World collision geometry.
11. Coarse and fine representations may serve different scopes, but their
    coordinate relationship must remain explicit.
