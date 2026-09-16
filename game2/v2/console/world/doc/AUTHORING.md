# Authoring Direction

The canonical runtime source remains the Game2 semantic world format: authors
describe a shape with `.`/`#`/`^` and immutable metadata. Future authoring
frontends such as LDtk, Tiled, or a custom grid editor may compile/import into
that `WorldDefinition` format:

```text
editor source -> compile/import -> canonical Game2 WorldDefinition/map
```

Engine must not depend directly on an editor's runtime format. Screen autotiling
is presentation logic: an author draws the semantic shape, and Screen chooses
top, edge, corner, or internal artwork from neighboring semantic tiles. Visual
tile IDs are never authoring or collision truth.
