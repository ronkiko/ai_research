# Game2 V2

Game2 V2 is a process-separated research environment. Its file tree makes the
five architectural domains explicit:

```text
game2/v2/
├── console/      virtual game console and its internal services
├── player/       external decision maker and model runtimes
├── training/     external learning domain
├── management/   operator and "god mode" plane
├── contracts/    public contracts shared across domains
└── tests/        cross-domain architectural verification
```

`console` owns the authoritative Engine, Controller, Display, internal
transport, lifecycle, and console configuration. `player` is outside the
Console and reaches gameplay through the public Joystick contract. `training`
is an external learning domain. `management` is an operator plane, not a
gameplay proxy. `contracts` is the only intentional shared public boundary
between independent domains.

Normative Console contract: [console/SPEC.md](console/SPEC.md)

System architecture: [ARCHITECTURE.md](ARCHITECTURE.md)

Cross-domain rules: [doc/DEPENDENCY_RULES.md](doc/DEPENDENCY_RULES.md)

## Entrypoints

Start the Console:

```bash
python -m game2.v2.console.main --config game2/v2/console/configs/unpaced-smoke.json
```

Start the external Scripted Player:

```bash
python -m game2.v2.player.scripted.main --manifest peripheral-manifest.json
```

Start the management placeholder:

```bash
python -m game2.v2.management.main
```

Run the realtime smoke, which starts Console and Player as separate processes:

```bash
python -m game2.v2.tests.harness \
  --config game2/v2/console/configs/realtime-smoke.json
```

Run V2 tests:

```bash
python -m unittest discover -s game2/v2/tests -v
```

Run all Game2 tests:

```bash
python -m unittest discover -s game2 -p 'test*.py' -v
```

This structural patch does not add a renderer, MLP, Trainer, REINFORCE, PPO,
VisionAdapter, audio, reward system, or management UI.
