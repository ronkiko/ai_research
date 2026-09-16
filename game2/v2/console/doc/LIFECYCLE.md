# Console Lifecycle

Console allocates the topology, starts Engine and waits for Engine READY, starts
Controller and waits for Controller READY, then starts optional Display. It
announces Console READY after required services are ready and publishes a
`PeripheralManifest` for an external Player. Display receives a private manifest
with the same map path, the STATE endpoint, and the selected `vision` or `screen`
mode.

Engine starts its fixed world clock without waiting for Player or Display.
Display startup or shutdown is spectator-local; a failed or closed renderer is
not a supervision signal for Engine.
