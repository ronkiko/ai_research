# Screen Renderer

The renderer composes, once per world load:

1. the three layered background PNGs;
2. reduced nearest-neighbor V2-owned tree, bush, and ruin background decorations;
3. semantic terrain rendered with `AutoTiler`;
4. a presentation-only hazard layer and compact finish flag.

Dynamic presentation draws a compact blue avatar placeholder over that cached
scene. The viewport is the full world size. It has no HUD, camera, scrolling, zoom,
animation system, operator controls, or input-to-Engine path.
