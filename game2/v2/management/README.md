# Management

The operator and "god mode" plane. Management may later select configs, launch
or stop independent Console, Player, and Training processes, and collect
operator telemetry.

Management, not Console, decides whether to launch the UI, Console, Player/model,
or Trainer and which experiment/configuration to use. Console receives no
management configuration and has no UI feature toggle.

Management is not a gameplay proxy and does not import runtime objects from
other domains. Current entrypoint: `main.py` placeholder. Local documentation:
`doc/`.

Future Management will choose the Player/model, training algorithm, Train or
Evaluate mode, World, seed, attempt count, Fresh or Resume mode, checkpoint,
and Start or Stop. It will consume Training metrics and results, but will not
compute learning semantics, serialize model state, or become a Trainer/UI
implementation in this architecture patch.
