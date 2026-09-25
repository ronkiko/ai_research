# Graphics rules

- Graphics is projection only. Never mutate world, controller, or VN state.
- RenderFrame cites one world epoch/tick/revision and one zone from one snapshot.
- Do not interpolate across zone or epoch changes.
- Browser consumes asset IDs and frames; it must not infer portals or scene transitions.
- Keep queues bounded/coalescing; renderer speed must not affect physics.
- Renderer output is not a default Spine/Motor sensor.
