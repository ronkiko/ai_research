# Vision

The Vision subsystem is the headless semantic branch of Display. It converts one
`WorldDefinition` and one validated latest state into an immutable world-resolution
`VisionFrame` containing one semantic class byte per pixel.

It has no Pygame, artwork, telemetry, Controller, Player, or Engine control
dependency. It is a renderer, not the future Player-facing VisionAdapter.
