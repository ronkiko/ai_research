# Game2 V2

Game2 V2 is a real-time AI-control laboratory. The Engine owns an autonomous
fixed-step world clock; inference, Training, rendering, and operator UI must
never gate world progress.

## Independent runtime blocks

Persistent world:

```bash
./game2/v2/boot.sh
```

Independent background Screen Server:

```bash
./game2/v2/op/screen_server.sh
```

Observe the persistent Console on Screen #1:

```bash
./game2/v2/op/screen.sh 1
```

Detach the Screen without stopping the world:

```bash
./game2/v2/op/screen.sh 1 off
```

## Training

Training is composed by Management from independent Console, Trainer, Model, and
Player processes.

Headless Training Set Level 1 from new weights:

```bash
./game2/v2/op/train.sh --fresh
```

`--fresh` explicitly resets the current Training Set checkpoints
(`planner.pt` and `motor.pt`) before starting.

Observe exactly the same Training on Screen #1:

```bash
./game2/v2/op/train.sh --fresh --screen 1
```

Continue the existing learned checkpoints:

```bash
./game2/v2/op/train.sh --resume
```

`--resume` fails if either checkpoint is missing.

`--screen` exists only in Management. It is never forwarded to Console,
Trainer, Model, or Player. When omitted, Training does not contact Screen Server
at all. If an already-bound Screen disappears after Training starts, learning
continues headless.

The default checkpoint directory is
`game2/v2/runtime/checkpoints/level-1`.

## Boundaries

`Vision` is a headless machine-facing Player peripheral.

`ScreenSource` is human-facing: Console converts private Engine STATE into
already-rendered RGB frames before they cross the Console boundary. Screen
Server never receives Engine STATE or Player Vision.

The independently testable runtime blocks are:

```text
Console
Player realtime shell
Model runtime
Trainer
Joystick
Vision
ScreenSource
Screen Server
Management Training composer
```

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
