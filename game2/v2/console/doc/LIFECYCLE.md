# Console Lifecycle

Console allocates the topology, starts Engine and waits for Engine READY, starts
Controller and waits for Controller READY, then starts optional Display. It
announces Console READY only after required services are ready and publishes a
`PeripheralManifest` for an external Player.

Engine starts its fixed world clock without waiting for Player or Display.
