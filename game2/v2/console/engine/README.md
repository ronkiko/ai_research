# Engine

The authoritative Console runtime subsystem. Engine owns the mutable Avatar,
Physics, fixed `world_tick`, transitional episode state, STATE, TELEMETRY, and
internal control service.
It receives an immutable `WorldDefinition` and materializes the local physics
instance from its collision geometry. Static map authoring belongs to the
neighboring `console/world/` domain.

Engine does not own Controller, Display, Player, training, management, or model
logic. It imports Console-private configuration/protocol and the World domain;
World does not import Engine.

`terminal` is the authoritative compatibility-actor result. Once it is
`success`, `dead`, or `timeout`, the avatar and Physics stop changing, scheduled
actions are cleared, and new gameplay actions are rejected. The global
`world_tick` continues independently. Compatibility reset recreates the current
actor and records its start tick; it never resets global time.

Process entrypoint: `main.py`; runtime: `engine.py`. Local documentation:
`doc/`.
