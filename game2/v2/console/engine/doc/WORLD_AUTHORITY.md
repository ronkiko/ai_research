# World Authority

`console/world/` owns the immutable `WorldDefinition`: the tile grid, semantic
tile IDs, spawn, goal, decorations, and collision rectangles derived from the
map. It has no runtime state and does not import Engine or Physics.

Engine is the only mutable owner of the Avatar and `WorldState`. On startup and
reset it creates an `AvatarBody` and local Physics `Surface` objects from the
immutable definition. Physics remains inside the Engine process and no external
domain receives the mutable world object.
