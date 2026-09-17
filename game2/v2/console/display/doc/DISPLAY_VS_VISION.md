# Display Versus Vision

Display owns two read-only rendering interfaces for one authoritative world:

```text
WorldDefinition + WorldState
           |             |
    Display.screen  Display.vision
       artwork       semantic raster
```

`vision` is a representation of what exists in the world, not raw
`x/y/vx/vy` telemetry. The public Player-facing sensory transport is:

```text
Engine STATE -> Display.vision -> VisionFrame -> public Vision peripheral -> Player
```

The public adapter does not expose raw Engine STATE. The temporary `vision.sh`
examiner is only a second subscriber and colorizes the same semantic bytes for
human viewing; it does not render from STATE or send actions.
