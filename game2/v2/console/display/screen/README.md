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


## Source ownership and multiple consumers

A Screen channel is produced by Console, not owned by a foreground window or by
Training Management. Console publishes the current ScreenSource discovery into
the Screen broker. The broker keeps one current source per channel and fans
source attach/detach announcements out to any number of consumers.

A consumer may connect before or after the Console source exists. Multiple
Screen windows, recorders, debuggers, or other observers may subscribe to the
same channel. They connect directly to the ScreenSource publisher, which already
supports multiple frame subscribers. The broker carries discovery/control only;
it does not proxy pixel frames.

Training Management may select the channel and render view when launching
Console, but it never binds or unbinds a particular consumer window.


On normal Console shutdown/map transition, the producer withdraws the channel
from the broker before closing the ScreenSource socket. Consumers therefore see
a normal DETACH/ATTACH handoff instead of an EOF while the stale source is still
advertised.
