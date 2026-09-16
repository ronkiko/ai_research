# Management Plane

Management is outside the gameplay data path. Process launch, config
generation, experiment lifecycle, and operator telemetry may be added here
without coupling management to Engine, Controller, Display, Player, or Trainer
objects.

In the future Management decides whether to launch the UI, Console,
Player/model, and Trainer, as well as which experiment/configuration to use.
Console does not receive Management configuration and does not decide whether a
management UI exists.
