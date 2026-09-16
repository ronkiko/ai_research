# Screen Autotiling

World stores only semantic `EMPTY`, `SOLID`, and `HAZARD` tiles. `AutoTiler` reads
the four cardinal neighbors of each tile and maps them to the cells that
actually exist in the copied `Tileset.png`:

```text
semantic grid + N/E/S/W topology -> (atlas x, atlas y)
```

The available solid cells provide top/interior and left/center/right variants.
Missing neighbors at the authored map boundary count as edges. Hazards use a
presentation-owned dark backing and deterministic spike strip because this pack
has no hazard atlas tile. Screen draws background decorations behind these
autotiled solids, so platform tops and the four-cell pit remain legible. No
visual tile ID or atlas coordinate is written to World or used by Physics.
