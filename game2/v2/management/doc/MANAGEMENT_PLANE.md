# Management Plane

Management is outside the gameplay data path. Process launch, config
generation, experiment lifecycle, and operator telemetry may be added here
without coupling management to Engine, Controller, Display, Player, or Trainer
objects.

The future Research Strategist is an autonomous research/meta-agent in this
control plane. It may reason for seconds or tens of seconds without blocking
Console, Player, Planner, Motor Controller, or a current Training run. It is
not a gameplay component and does not control the Joystick.

In the future Management decides whether to launch the UI, Console,
Player/model, and Trainer, as well as which experiment/configuration to use.
Console does not receive Management configuration and does not decide whether a
management UI exists.

Management may eventually expose explicit tool/capability boundaries for:

- observations and experiment metrics;
- training and evaluation requests;
- candidate comparison, selection, and safe activation;
- publication of optional StrategyGuidance;
- communication with the Operator.

These tools are conceptual only in this patch. Strategist receives public or
derived evidence and declared experiment inputs, not private Engine state,
physics coordinates, hidden collision geometry, `ActionCommand`,
`target_world_tick`, or debug-only ground truth. Management must preserve the
domain dependency direction and must not import Player or Training runtime
internals.
