# Vision Renderer

Static terrain is expanded and cached when the renderer receives a world. Each
render copies that semantic raster, overlays the goal, then overlays OTHER
Actors and the selected perspective Actor. The deterministic priority is
`SELF > OTHER_ACTOR > GOAL > HAZARD > SOLID > EMPTY`.

Semantic class 3 is `SELF`; class 5 is `OTHER_ACTOR`. The same multi-Actor
STATE can therefore produce different frames for different `self_actor_id`
perspectives without exposing data outside STATE and immutable World.

The current raster resolution is `WorldDefinition.width` by
`WorldDefinition.height`, so an avatar at continuous pixel coordinates remains
visible between tile boundaries. `VisionFrame` is frozen and stores bytes only;
it is not a privileged state or telemetry record.
