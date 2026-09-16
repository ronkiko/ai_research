# Engine

The authoritative Console world subsystem. Engine owns the map, Avatar,
physics, fixed clock, episodes, state, telemetry, and internal control service.

Engine does not own Controller, Display, Player, training, management, or model
logic. It imports Console-private configuration/protocol and local world code.

Process entrypoint: `main.py`; runtime: `engine.py`. Local documentation:
`doc/`.
