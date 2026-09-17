# Engine

The authoritative Console runtime subsystem. Engine owns the mutable Avatar,
Physics, fixed clock, episodes, state, telemetry, and internal control service.
It receives an immutable `WorldDefinition` and materializes the local physics
instance from its collision geometry. Static map authoring belongs to the
neighboring `console/world/` domain.

Engine does not own Controller, Display, Player, training, management, or model
logic. It imports Console-private configuration/protocol and the World domain;
World does not import Engine.

`terminal` is the authoritative episode result. Once it is `success`, `dead`,
or `timeout`, the episode clock, avatar, and Physics stop changing, scheduled
actions are cleared, and new gameplay actions are rejected. The session clock
can continue independently; reset/restart is a future explicit contract.

Process entrypoint: `main.py`; runtime: `engine.py`. Local documentation:
`doc/`.
