# Vision

`Display.vision` is a headless semantic renderer. It expands each semantic World
tile to the world-resolution raster, preserving continuous sub-tile avatar
movement. It uses no artwork and imports no Pygame.

The local class IDs are:

| ID | Class |
|---:|---|
| 0 | `EMPTY` |
| 1 | `SOLID` |
| 2 | `HAZARD` |
| 3 | `AVATAR` |
| 4 | `GOAL` |

Composition is deterministic and documented as `AVATAR > GOAL > HAZARD > SOLID
> EMPTY`. `VisionFrame` contains only `width`, `height`, `pixels`, and
`world_tick`; velocity, grounded state, collision rectangles, and action
diagnostics are not frame metadata. In `display_mode: "vision"`, the same frame
is sent as raw semantic bytes through the public Vision peripheral.
