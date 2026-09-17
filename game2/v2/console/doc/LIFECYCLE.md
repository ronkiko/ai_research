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
or Display. Player is not the owner of the clock. In the target model, Player
attach and detach do not start or stop World.
Display startup or shutdown is spectator-local; a failed or closed renderer is
not a supervision signal for Engine.
