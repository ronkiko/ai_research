# Game2 V2

Game2 V2 is a real-time AI-control laboratory. The Engine owns an autonomous
fixed-step world clock; inference, Training, rendering, and operator UI must
never gate world progress.

## Screen workflow

Start the background Screen broker once:

```bash
./game2/v2/op/screen_server.sh
```

A Screen window is a consumer and may be opened either before or after a source
exists:

```bash
./game2/v2/op/screen.sh 1
```

That command is foreground by design. It owns one native Pygame consumer window
and stays running until you close the window or press Esc. With no producer it
shows `Waiting for source...`. Multiple consumers may subscribe to the same
numbered channel.

Start Training independently:

```bash
./game2/v2/op/train.sh --fresh --screen 1
```

Training never owns a graphical consumer. It selects the Screen channel/view
when launching Console; Console owns the current `ScreenSource` and publishes
that source into Screen Server. Screen Server keeps one current source per
channel and fans attach/detach discovery out to zero or more consumers. On map
changes the old Console withdraws its source before shutdown and the new Console
publishes the replacement.

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

`--fresh` clears the episode store and all four checkpoints before starting.

For faster headless collection with live terminal progress:

```bash
./game2/v2/op/train.sh --fresh --mode unpaced
./game2/v2/op/train.sh --resume --mode unpaced
```

The live bar shows elapsed world ticks within an attempt; `goal` shows the
best progress toward the target in that attempt. PPO has a separate batch
indicator. Deterministic verification without learning runs after a successful
stochastic attempt, every five updates, and at the attempt limit. A map is
learned only after three consecutive frozen successes. Before the set can pass,
the final frozen model must again pass every training map three times in a row.
This is a training-map check, not an Exam.

Ctrl+C stops training and retains the last completed checkpoint. Use `--resume`
to continue; `--max-episodes-per-map N` limits the attempts in a run. Redirected
output retains periodic progress lines; `--json` exposes detailed events.

Continue existing checkpoints:

```bash
./game2/v2/op/train.sh --resume
```

Read-only qualification of the saved model, with no PPO/update/checkpoint save:

```bash
./game2/v2/op/screen.sh 1
./game2/v2/op/train.sh --player player1 --verify --screen 1 --view vision
```

`--verify` loads the saved checkpoint and runs every Training Set map with
learning OFF. Each map must pass three consecutive frozen runs. Verification
uses a temporary episode store, so the persistent training datasets and
curriculum checkpoint state are not modified.

Observe Training on an already-open Screen with the normal human render:

```bash
./game2/v2/op/train.sh --fresh --screen 1
```

Show the actual logical Grid Vision used for CNN input instead:

```bash
./game2/v2/op/train.sh --fresh --screen 1 --view vision
```

The Vision spectator renders fine physics and metadata from the public
`VisionGrid`, with solid 20x12 tile boundaries, dashed 8x8 subdivisions,
pixel rulers starting at world origin `0,0`, and a compact Grid Vision legend.
It does not display private Engine `x/y/vx/vy` telemetry.

`--screen N` requires the Screen Server broker to be reachable, but it does
not require a consumer window to be open. Consumers may attach later. Losing or
closing a Screen consumer remains spectator-only and does not affect the
learning topology.

## Boundaries

`Vision` is a headless machine-facing Player peripheral. Its multi-scale Grid Vision contract is documented in [`console/display/vision/README.md`](console/display/vision/README.md).

`ScreenSource` is human-facing: Console converts private Engine STATE into
rendered RGB frames before they cross the Console boundary.

`Screen Server` is only a broker of numbered slots and bindings. It does not
create Pygame windows.

`op/screen.sh N` owns the graphical Screen. The separate Bot Profiler
Management editor is launched with `./game2/v2/op/bot_profiler.sh`; see
[Management](management/README.md#bot-profiler) for editing and verification.

## Tests

```bash
python -m unittest discover -s game2/v2/tests -v
python -m game2.v2.tests.server_smoke
```
