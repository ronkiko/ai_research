# Management Plane

Management is outside the gameplay data path. Process launch, experiment
lifecycle, operator presentation, and telemetry may live here only when they do
not become runtime bridges between Console, Player, Model, or Trainer.

## Screen Server

`management/screen_server.py` is an independent long-lived operator service.
`op/screen_server.sh` starts it in the background. The service publishes a
strict discovery record and exposes numbered screen slots.

The current corrective cut intentionally stops at lifecycle and slot ownership:
all slots report `idle`. A later patch may bind a Console presentation source to
a selected slot through a dedicated contract.

Required invariants:

- Screen Server does not import Console, Player, or Training runtime modules.
- Screen Server does not consume Player Vision as a human-display shortcut.
- Screen presence is optional.
- Screen failure never stops Console, Player, Model, or Trainer.
- Human observation never becomes a Training prerequisite.

## Future Experiment Composition

Management may later select configs and launch independent Console,
Player/model, and Trainer processes. Composition must happen through process
boundaries and executable contracts rather than by importing runtime objects.

The future Research Strategist is an autonomous research/meta-agent in this
control plane. It may reason asynchronously without blocking Console, Player,
Planner, Motor Controller, or Training. It is not a gameplay component and does
not control the Joystick.

Mode-scoped capabilities remain:

- **Training:** inspect Training Set Levels and Training Maps; request training,
  replay, evaluation, candidate comparison, and safe activation.
- **Exam:** request an Exam Run and receive PASS/FAIL plus aggregate
  certification metrics only.
- **Free Play:** observe aggregate behavior; optionally collect permitted
  experience; and optionally request continued training.
- **All modes:** receive allowed observations and experiment metrics and
  communicate with the Operator.

Management must preserve domain dependency direction and must not expose private
Engine state, physics coordinates, hidden collision geometry, `ActionCommand`,
`target_world_tick`, or debug-only ground truth.
