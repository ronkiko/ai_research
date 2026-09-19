# Console

Console owns the authoritative World, Engine, Controller, private transport, and
Console-side presentation producers. It does not own Player, Model, Trainer,
Management, or native operator windows.

The persistent server is started by:

```bash
./game2/v2/boot.sh
```

It remains valid with zero Players and zero Actors.

For machine Players, each attached Player receives its own public Joystick and
headless semantic Vision capabilities.

For human observation, Console also starts one headless `ScreenSource`. It
consumes private Engine STATE inside the Console domain, renders the normal
human-facing artwork off-screen, and publishes only RGB `ScreenFrame` pixels.
The source discovery is published separately from Player discovery at
`game2/v2/runtime/current-screen-source.json`.

A ScreenSource failure is spectator-only: it must not stop Engine or Player
gameplay. Screen Server is external Management infrastructure and never receives
raw Engine STATE.

Normative contract: [`SPEC.md`](SPEC.md).
