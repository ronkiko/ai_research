# Screen

The Screen subsystem is the human-facing Display branch. It loads the copied
assets in `assets/`, builds a cached static scene, and draws the latest
multi-Actor state on each presentation frame. The self Actor keeps the primary
presentation style; other Actors use a distinct simple style. The scene order
is background, background decorations, terrain, hazard presentation, goal
marker, and Actors.

Screen handles only window-close and `ESC`. It does not accept gameplay input or
send messages to Engine. See `doc/SCREEN_RENDERER.md` and
`doc/AUTOTILING.md`.


## Persistent Screen recovery

The Screen window is an operator spectator and must not define Engine or Training
lifetime. If its frame source socket closes, the window keeps the current source
identity and retries the connection. The Console server supervises ScreenSource
and restarts it on process failure while the Engine remains alive. Explicit
DETACH on map transition cancels retries until the next source is bound.
