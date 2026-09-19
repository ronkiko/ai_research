# Display Versus Vision

Human Screen and machine Vision are separate observations of one authoritative
world:

```text
WorldDefinition + WorldState
           |             |
           |             +--> ScreenRenderer --> RGB --> human
           |
           +--> VisionGridRenderer --> two logical matrices --> Player
```

Screen owns artwork and presentation. Vision owns no artwork and no raster.

The public Player-facing path is:

```text
Engine STATE -> Display.vision -> VisionGrid -> public Vision peripheral -> Player
```

`VisionGrid.physics` comes from authored World tiles.
`VisionGrid.metadata` contains independent `GOAL`, `SELF`, and
`OTHER_ACTOR` bits for intersected cells.

Raw Engine STATE remains private. Vision does not expose `x/y/vx/vy`,
grounded state, collision rectangles, input state, or telemetry.
