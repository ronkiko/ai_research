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
