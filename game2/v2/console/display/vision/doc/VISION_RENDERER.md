# Vision Renderer

Static terrain is expanded and cached when the renderer receives a world. Each
render copies that semantic raster, overlays the goal, then overlays the avatar.
The deterministic priority is `AVATAR > GOAL > HAZARD > SOLID > EMPTY`.

The current raster resolution is `WorldDefinition.width` by
`WorldDefinition.height`, so an avatar at continuous pixel coordinates remains
visible between tile boundaries. `VisionFrame` is frozen and stores bytes only;
it is not a privileged state or telemetry record.
