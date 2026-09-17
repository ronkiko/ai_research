# Controller

The Console input subsystem. Controller owns the Joystick listener, the only
Engine CONTROL client, input translation, finite hold behavior, lead scheduling,
and Joystick ACK mapping. Its private manifest fixes one `actor_id`; telemetry
and ActionCommand scheduling are scoped to that Actor while `target_world_tick`
remains global.

It does not own the Player or the world. It imports the public Joystick contract
and Console-private Engine protocol. Process entrypoint: `main.py`; runtime:
`controller.py`. Local documentation: `doc/`.
