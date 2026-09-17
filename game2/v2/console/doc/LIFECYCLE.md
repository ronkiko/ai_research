# Console Lifecycle

Console allocates the private topology, starts Engine, waits for Engine READY,
then starts the Player attach listener. It publishes
`runtime/current-console.json` atomically only after both are ready and
announces Console READY. The canonical server has no global Controller or
Display. Each ATTACH allocates a strict public `PlayerManifest`, starts one
Controller and one headless Vision Display, and waits for both READY signals.
Display receives a private manifest with the shared STATE endpoint, map path,
`vision` mode, and its own Vision endpoint. See
[MMO_SERVER_MODEL.md](MMO_SERVER_MODEL.md).

Engine starts the global `world_tick` with Console, without waiting for Player
or Display. Core Engine construction creates zero Actors. The temporary demo
composition explicitly registers the compatibility Player binding and spawns
its Actor; this does not make it a global Engine default. Dynamic ATTACH creates
no Actor. START sends a private scoped SPAWN command, while Player attach and
detach do not start or stop World.
Display startup or shutdown is spectator-local; a failed or closed renderer is
not a supervision signal for Engine.

Actor lifecycle is local to the World runtime: `ABSENT`, `ACTIVE`, and
`TERMINAL`. `respawn` recreates one Actor body at the current World spawn and
preserves `world_tick` and every other Actor. `despawn` removes only that
Actor's binding and scheduled input.

Patch 3 status:

- implemented: global `world_tick`, Actor registry, 0..N core Actors,
  actor-scoped input/result/respawn, multi-Actor STATE, SELF/OTHER Vision;
- implemented: persistent `boot.sh`, dynamic Connection attach/discovery,
  per-Player public capability allocation, explicit `START`, attach-only
  `vision.sh`, public terminal events, actor-local respawn, and detach cleanup.

Training Episodes remain outside Console. They are experiment/training records,
not Engine or Player lifecycle state.
