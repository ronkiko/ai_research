# Screen Versus Vision

Screen and vision are two views, not two worlds. Both receive the same immutable
WorldDefinition and latest authoritative WorldState-derived view:

| Interface | Consumer | Output |
|---|---|---|
| `screen` | human | Pygame artwork and a game window |
| `vision` | future machine sensor | deterministic semantic `VisionFrame` |

Changing a tileset or screen autotile mapping cannot change World semantics,
collision geometry, or Engine timing. A future Player VisionAdapter may transport
`VisionFrame`; implementing that adapter is outside this patch.
