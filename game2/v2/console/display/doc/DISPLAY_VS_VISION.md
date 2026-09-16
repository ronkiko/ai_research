# Display Versus Vision

Display owns two read-only rendering interfaces for one authoritative world:

```text
WorldDefinition + WorldState
           |             |
    Display.screen  Display.vision
       artwork       semantic raster
```

`vision` is a representation of what exists in the world, not raw
`x/y/vx/vy` telemetry. A future `VisionAdapter` remains a separate Player-facing
sensory contract and transport layer:

```text
Engine STATE -> Display.vision -> VisionFrame -> future Vision peripheral -> Player
```

That future adapter must not expose raw Engine STATE. Management/God Mode may
later display both implementations side by side, but Management is not changed
by this patch.
