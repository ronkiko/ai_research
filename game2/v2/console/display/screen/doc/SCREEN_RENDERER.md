# Screen Renderer

The renderer composes, once per world load:

1. the three layered background PNGs;
2. semantic terrain rendered with `AutoTiler`;
3. V2-owned tree, bush, and ruin decorations;
4. a presentation-only goal marker.

Dynamic presentation draws a blue outlined avatar over that cached scene. The
viewport is the full world size. It has no HUD, camera, scrolling, zoom,
animation system, operator controls, or input-to-Engine path.
