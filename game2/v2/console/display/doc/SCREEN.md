# Screen

`Display.screen` is the human-facing presentation. It uses only V2-owned assets,
the semantic WorldDefinition, and the latest validated avatar view. The viewport
is exactly the current world dimensions: there is no camera, scrolling, zoom, or
gameplay input.

Window events are limited to `QUIT` and `ESC`, which close this spectator process.
They never reach Controller or Engine. The static background, autotiled terrain,
decorations, and goal marker are built once; each frame redraws that cached scene
and the current avatar.
