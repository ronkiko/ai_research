# Game2 V2

Game2 V2 is a real-time AI-control laboratory. The Engine owns an autonomous
fixed-step world clock; inference, Training, rendering, and operator UI must
never gate world progress.

## Screen workflow

Start the background Screen broker once:

```bash
./game2/v2/op/screen_server.sh
```

In a second terminal, open Screen #1 **before** starting Training:

```bash
./game2/v2/op/screen.sh 1
```

That command is foreground by design. It owns the native Pygame window and stays
running until you close the window or press Esc. Initially it shows
`Waiting for source...`.

In a third terminal start Training:

```bash
./game2/v2/op/train.sh --fresh --screen 1
```

Training never launches a graphical process. It only asks Screen Server to bind
the current Console `ScreenSource` to the already-open Screen #1. On map changes
the same window stays open while the source is rebound.

Useful broker commands:

```bash
./game2/v2/op/screen.sh status
./game2/v2/op/screen.sh bind 1
./game2/v2/op/screen.sh unbind 1
./game2/v2/op/screen.sh close 1
```

`bind 1` manually attaches the currently running persistent Console, which is
useful with `./game2/v2/boot.sh`.

## Training

Headless new Training Set Level 1:

```bash
./game2/v2/op/train.sh --fresh
```

`--fresh` resets `planner.pt` and `motor.pt` before starting.

Continue existing checkpoints:

```bash
./game2/v2/op/train.sh --resume
```

Observe Training on an already-open Screen:

```bash
./game2/v2/op/train.sh --fresh --screen 1
```

If `--screen N` is supplied but Screen N is not already open, Training fails
preflight with a clear operator instruction. Once Training has started, losing
the Screen remains spectator-only and does not affect the learning topology.

## Boundaries

`Vision` is a headless machine-facing Player peripheral.

`ScreenSource` is human-facing: Console converts private Engine STATE into
rendered RGB frames before they cross the Console boundary.

`Screen Server` is only a broker of numbered slots and bindings. It does not
create Pygame windows.

`op/screen.sh N` is the only graphical operator process.

## Tests

```bash
python -m unittest discover -s game2/v2/tests -v
python -m game2.v2.tests.server_smoke
```
