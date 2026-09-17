# World Model

`WorldDefinition` is a frozen, read-only description of one Console scene. It
contains:

- `map_id` and `name`;
- `tile_size`, grid dimensions, and pixel `width`/`height`;
- an immutable semantic tile grid;
- pixel-space `spawn` and `goal` rectangles;
- immutable collision rectangles derived from the grid;
- immutable decoration metadata.

The grid is the authoring source of truth. Collision rectangles are derived
data and may merge equal horizontal tile runs to keep the Physics hot path
small. Decorations never create collision geometry.

World has no live Actor body, velocity, grounded flag, episode, tick, action
queue, model, joystick, or renderer. Engine creates one shared Physics rules
object and independent `ActorBody` values from this definition, then owns all
mutable gameplay state.

Goal completion is a pure rule over primitive Actor geometry and state: the
Actor must be alive, grounded, and fully contained by the goal rectangle.

Future Display consumers are read-only presentations of `WorldDefinition` and
`WorldState`. A human `screen` renderer may use artwork, while a model-facing
`vision` renderer may use semantic tile IDs. Neither mutates World or controls
Physics.
