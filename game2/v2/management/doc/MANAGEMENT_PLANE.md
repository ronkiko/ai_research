# Management Plane

Management is outside the gameplay data path. Process launch, experiment
lifecycle, operator presentation, and telemetry may live here only when they do
not become runtime bridges between Console, Player, Model, or Trainer.

## Screen Broker

`management/screen_server.py` is an independent long-lived broker started by:

```bash
./game2/v2/op/screen_server.sh
```

It owns only numbered slot registration and source binding. It does **not**
launch Pygame and does not own graphical windows.

A graphical Screen is a separate foreground operator process:

```bash
./game2/v2/op/screen.sh 1
```

The window registers slot 1 with Screen Server and remains alive while waiting,
bound, unbound, or between Training maps. Closing that terminal/window removes
only that Screen registration.

Training `--screen N` performs only BIND/UNBIND against an already-open slot.
If the requested slot is not open at preflight, Training does not start.

Required invariants:

- Screen Server does not import Console, Player, or Training runtime modules.
- Screen Server never launches the graphical Screen.
- Screen does not consume Player Vision as a human-display shortcut.
- Screen failure after Training start never stops Console, Player, Model, or Trainer.
- Human observation never enters the gameplay hot path.
- Console exposes only rendered ScreenSource frames, never raw Engine STATE.

## Experiment Composition

Management composes independent Console, Player/model, and Trainer processes
through executable contracts rather than runtime imports. The graphical Screen
remains outside that ownership tree.

Management must preserve domain dependency direction and must not expose private
Engine state, physics coordinates, hidden collision geometry, `ActionCommand`,
`target_world_tick`, or debug-only ground truth.
