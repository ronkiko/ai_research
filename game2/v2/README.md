# Game2 V2

Game2 V2 is the virtual-console foundation for the Game2 behavioral research
environment. An external Player reaches the world only through the Joystick;
the Console owns Engine, Controller, and optional Display.

```text
External Player
     |
  Joystick
     |
 Controller
     |
  Engine
     |
  Display
```

## Current Stage

The fixed-step Engine, narrow subsystem capability manifests, Controller input
bridge, and Display STATE process boundary are implemented. Display does not
render video yet. Model runtime, Trainer, VisionAdapter, and UI remain external
or future work.

Canonical entrypoint: `console.py`

Normative spec: [CONSOLE_SPEC.md](CONSOLE_SPEC.md)

Architecture overview: [ARCHITECTURE.md](ARCHITECTURE.md)

## Run

Start the Console without an external Player:

```bash
python -m game2.v2.console --config game2/v2/configs/realtime-smoke.json
```

Run the realtime integration smoke. The test-only harness starts Console and
ScriptedPlayer as separate processes:

```bash
python -m game2.v2.tests.harness --config game2/v2/configs/realtime-smoke.json
```

Run the unpaced Console smoke without a Player:

```bash
python -m game2.v2.console --config game2/v2/configs/unpaced-smoke.json
```

Run V2 tests:

```bash
python -m unittest discover -s game2/v2/tests -v
```

Run all Game2 tests:

```bash
python -m unittest discover -s game2 -p 'test*.py' -v
```

## Deliberate Non-Goals

No MLP, REINFORCE, PPO, reward or training API, renderer, cockpit UI,
VisionAdapter, audio, or model/trainer integration is part of this foundation.
