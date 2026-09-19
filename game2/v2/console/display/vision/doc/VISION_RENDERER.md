# VisionGridRenderer

`VisionGridRenderer` is a deterministic logical sensor, not an image renderer.

The first matrix, `physics`, is a byte-for-byte logical projection of authored
World tiles: `EMPTY`, `SOLID`, or `HAZARD`.

The second matrix, `metadata`, is a bit mask. `GOAL`, `SELF`, and
`OTHER_ACTOR` are ORed into every grid cell intersected by their rectangle.
Because metadata is independent from physics and uses bits, overlap never
destroys information.

Different Player perspectives share identical physics and Goal metadata. Only
which Actor receives `SELF` versus `OTHER_ACTOR` changes.

The output size is always `columns × rows`; `tile_size` is carried explicitly
so a Player can interpret cell geometry without receiving private Engine STATE.
