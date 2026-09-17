# Human Keyboard Player

The Human Keyboard Player is an external Player process when used by its
canonical CLI. It reads only the public `PeripheralManifest`, connects to its
`joystick` endpoint, and sends the version 1 two-button Joystick state at its
own 120 Hz input cadence. The temporary embedded demo uses the same
`HumanJoystickClient` and `HumanKeyboardInput` in-process.

```text
keyboard -> HumanKeyboardInput -> HumanJoystickClient -> public Joystick -> Console Controller -> Engine
```

Controls:

- `Right Arrow` or `D`: hold `right=true`;
- `Space`, `Up Arrow`, or `W`: hold `jump=true`;
- `Esc` or closing the Player window: stop only the Player process.

`HumanKeyboardInput` creates no window and never drains Pygame events; its host
owns event routing. The Player has no Engine access and does not import Console,
Display, World, or the private action protocol.

Canonical invocation:

```bash
python -m game2.v2.player.human.main --manifest /path/to/peripheral-manifest.json
```

Local documentation: `doc/HUMAN_PLAYER.md`.
