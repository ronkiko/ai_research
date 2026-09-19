# Domain Model

These domains are not only organizational boundaries. They also protect
independent timing domains: Player/model latency is not Engine/world timing,
and UI or training work must not become a hidden physics synchronization point.

## Console

The virtual game console owns world authority, fixed-step execution, input
translation, state display consumption, internal channels, lifecycle, and
private topology manifests. It does not own a Player, model, Trainer, or
management UI.

## Player

The external Player is the realtime gameplay shell and uses public peripheral
contracts. Its learned Model runtime contains the Planner / Policy and Motor
Controller; Player forwards public Vision and emits completed decisions through
the public Joystick contract. The Player remains functional without the
optional Research Strategist. Human and scripted Players are valid
alternatives. A Player does not import Console implementation modules.

## Training

Training is an external learning system. Future trainers and model-training
contracts belong here. Trainer targets a trainable component or candidate, not
necessarily an entire Player. Training does not access Console internals or
world objects and is not a fourth gameplay-intelligence layer.

## Management

Management is the operator and control plane. A future Research Strategist is
an autonomous research/meta-agent in this domain; it may use explicit tools to
observe results, request Train/Evaluate, compare and activate candidates, and
publish optional guidance. Management may choose configurations and launch or
stop independent processes. It is not a gameplay proxy and does not own runtime
objects from other domains.

## Contracts

Contracts are the leaf domain: public Joystick, public capabilities, and generic
wire framing. Logical future boundaries such as StrategyGuidance, MotorGoal,
and ActionDecision must remain explicit contracts rather than hidden runtime
coupling. Contracts describe allowed messages and do not import a domain that
consumes them.
