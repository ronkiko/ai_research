# Human Keyboard Player

The Human Keyboard Player is an external Player process. It reads only the
public `PeripheralManifest`, connects to its `joystick` endpoint, and sends the
version 1 two-button Joystick state at its own 120 Hz input cadence.

```text
keyboard -> Human Player -> public Joystick -> Console Controller -> Engine
```

Controls:

- `Right Arrow` or `D`: hold `right=true`;
- `Space`, `Up Arrow`, or `W`: hold `jump=true`;
- `Esc` or closing the Player window: stop only the Player process.

The Player has no Engine access and does not import Console, Display, World, or
the private action protocol.

Canonical invocation:

```bash
python -m game2.v2.player.human.main --manifest /path/to/peripheral-manifest.json
```

Local documentation: `doc/HUMAN_PLAYER.md`.
