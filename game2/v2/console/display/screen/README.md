# Screen

The Screen subsystem is the human-facing Display branch. It loads the copied
assets in `assets/`, builds a cached static scene, and draws only the latest avatar
on each presentation frame. The scene order is background, background
decorations, terrain, hazard presentation, goal marker, and avatar.

Screen handles only window-close and `ESC`. It does not accept gameplay input or
send messages to Engine. See `doc/SCREEN_RENDERER.md` and
`doc/AUTOTILING.md`.
