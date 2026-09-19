# Game2 V2

Game2 V2 is a real-time AI-control laboratory. The Engine owns an autonomous
fixed-step world clock; inference, Training, rendering, and operator UI must
never gate world progress.

## Independent runtime blocks

Start the persistent Console:

```bash
./game2/v2/boot.sh
```

Start the independent background Screen Server:

```bash
./game2/v2/op/screen_server.sh
```

Bind Screen #1 to the currently running Console:

```bash
./game2/v2/op/screen.sh 1
```

Detach it without stopping the Console:

```bash
./game2/v2/op/screen.sh 1 off
```

Show all slots:

```bash
./game2/v2/op/screen.sh
```

The Console publishes a read-only `ScreenSource` containing already-rendered
RGB frames. Screen Server never receives Engine STATE, Vision, Joystick, Player
internals, Model data, or Trainer data. Closing a Screen window only frees that
Screen slot.

`Vision` remains a separate headless machine-facing Player peripheral.
Screen is human-facing only.

The retained runtime blocks are independently testable:

```text
Console
Player realtime shell
Model runtime
Trainer
Joystick
Vision
ScreenSource
Screen Server
```

Training composition is intentionally not rebuilt in this patch. The next
composition step may optionally bind a Training Console's ScreenSource to a
numbered Screen; headless Training must remain the default.

## Architecture

- [Architecture overview](ARCHITECTURE.md)
- [Realtime contract](doc/REALTIME_SYSTEM.md)
- [Dependency rules](doc/DEPENDENCY_RULES.md)
- [Console specification](console/SPEC.md)

## Tests

```bash
python -m unittest discover -s game2/v2/tests -v
python -m game2.v2.tests.server_smoke
```
