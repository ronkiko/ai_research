# Engine

The authoritative Console runtime subsystem. Engine owns the mutable Avatar,
Physics, fixed clock, episodes, state, telemetry, and internal control service.
It receives an immutable `WorldDefinition` and materializes the local physics
instance from its collision geometry. Static map authoring belongs to the
neighboring `console/world/` domain.

Engine does not own Controller, Display, Player, training, management, or model
logic. It imports Console-private configuration/protocol and the World domain;
World does not import Engine.

Process entrypoint: `main.py`; runtime: `engine.py`. Local documentation:
`doc/`.
