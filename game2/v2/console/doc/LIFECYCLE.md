# Console Lifecycle

Console allocates the topology, starts Engine and waits for Engine READY, starts
Controller and waits for Controller READY, then starts optional Display. It
announces Console READY after required services are ready and publishes a
`PeripheralManifest` for an external Player. In `vision` mode that public
manifest includes the Vision endpoint. Display receives a private manifest with
the same map path, the STATE endpoint, the selected `vision` or `screen` mode,
and its Vision bind endpoint. See the target MMO lifecycle in
[MMO_SERVER_MODEL.md](MMO_SERVER_MODEL.md).

Engine starts the global `world_tick` with Console, without waiting for Player
or Display. Core Engine construction creates zero Actors. The temporary demo
composition then explicitly registers the compatibility Player binding and
spawns its Actor; this does not make it a global Engine default. Player is not
the owner of the clock. In the target model, Player attach and detach do not
start or stop World.
Display startup or shutdown is spectator-local; a failed or closed renderer is
not a supervision signal for Engine.

Actor lifecycle is local to the World runtime: `ABSENT`, `ACTIVE`, and
`TERMINAL`. `respawn` recreates one Actor body at the current World spawn and
preserves `world_tick` and every other Actor. `despawn` removes only that
Actor's binding and scheduled input.

Patch 2 status:

- implemented: global `world_tick`, Actor registry, 0..N core Actors,
  actor-scoped input/result/respawn, multi-Actor STATE, SELF/OTHER Vision;
- Patch 3: persistent `boot.sh`, dynamic Connection attach/discovery,
  per-Player public capability allocation, explicit `START`, attach-only
  `vision.sh`, and examiner respawn flow.
