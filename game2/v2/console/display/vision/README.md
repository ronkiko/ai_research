# Display Vision

The Vision subsystem converts `WorldDefinition + DisplayState` into a compact,
headless `VisionGrid`.

It has no Pygame dependency and no image pipeline. Static terrain is copied
directly from the World tile matrix into `physics`. Goal and Actor occupancy
are ORed into the independent `metadata` bit-mask matrix.

See [doc/VISION_RENDERER.md](doc/VISION_RENDERER.md).
