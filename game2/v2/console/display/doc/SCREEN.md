# Screen

`Display.screen` is the human-facing presentation. It uses only V2-owned assets,
the semantic WorldDefinition, and the latest validated avatar view. The viewport
is exactly the current world dimensions: there is no camera, scrolling, zoom, or
gameplay input.

Window events are limited to `QUIT` and `ESC`, which close this spectator process.
They never reach Controller or Engine. The static background, autotiled terrain,
decorations, and goal marker are built once; each frame redraws that cached scene
and the current avatar. The Screen presentation loop runs at the 60 FPS target,
independently of Engine physics cadence, and renders only a newer latest STATE;
it never replays a backlog.

When authoritative STATE contains `success`, `dead`, or `timeout`, Screen draws a
simple result overlay over the frozen scene (`VICTORY`, `GAME OVER`, or `TIME OUT`).
The overlay never sends commands. In embedded mode the shell, rather than Screen,
owns `QUIT`, focus, keyboard events, and the display flip.
