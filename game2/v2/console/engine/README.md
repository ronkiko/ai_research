# Engine

The authoritative Console runtime subsystem. Engine owns the mutable World
runtime, shared Physics, fixed `world_tick`, 0..N Actors, STATE, TELEMETRY, and
internal control service.
It receives an immutable `WorldDefinition` and materializes the local physics
instance from its collision geometry. Static map authoring belongs to the
neighboring `console/world/` domain.

Engine does not own Controller, Display, Player, training, management, or model
logic. It imports Console-private configuration/protocol and the World domain;
World does not import Engine.

Each Actor owns its result and scheduled actions. Once an Actor is `success`,
`dead`, or `timeout`, that Actor's body stops changing, its actions are cleared,
and new gameplay actions for it are rejected. Other Actors and the global
`world_tick` continue independently. Actor-local respawn recreates one body and
records its new start tick; it never resets global time.

Process entrypoint: `main.py`; runtime: `engine.py`. Local documentation:
`doc/`.
