# Map Format

The current V2 map schema is deliberately a hand-editable square tile grid.
The JSON object has exactly these fields:

| Field | Meaning |
|---|---|
| `schema_version` | Must be `2` |
| `name` | Non-empty map name, at most 100 characters |
| `tile_size` | Must be `64` pixels |
| `columns`, `rows` | Grid dimensions, each from 2 through 64 |
| `spawn` | Tile rectangle: `column`, `row`, `columns`, `rows` |
| `terrain` | Exactly `rows` strings, each exactly `columns` symbols |
| `goal` | Tile rectangle with the same four fields |
| `decorations` | List of `sprite`, `column`, `baseline` metadata |

All rectangle positions and sizes are integer tile coordinates. Spawn and goal
must fit inside the map, and the goal must be large enough for the avatar.
Decoration sprites are currently `tree`, `bush`, and `ruin`; decorations do
not create physics geometry.

Example terrain remains easy to draw by hand:

```text
....................
..........###.......
.....^^.........####
####################
```

The loader keeps this simple tile authoring representation and derives merged
collision rectangles. No scene editor, vector geometry, external map format,
or renderer asset is part of the map schema.
