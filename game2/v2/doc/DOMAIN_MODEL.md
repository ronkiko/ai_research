# Domain Model

## Console

The virtual game console owns world authority, fixed-step execution, input
translation, state display consumption, internal channels, lifecycle, and
private topology manifests. It does not own a Player, model, Trainer, or
management UI.

## Player

The external Player chooses actions and uses public peripheral contracts. A
scripted Player is one implementation; a future model runtime is another. A
Player does not import Console implementation modules.

## Training

Training is an external learning process. Future trainers and model-training
contracts belong here. Training does not access Console internals or world
objects.

## Management

Management is the operator plane. It may choose configurations and launch or
stop independent processes. It is not a gameplay proxy and does not own
runtime objects from other domains.

## Contracts

Contracts are the leaf domain: public Joystick, public capabilities, and generic
wire framing. Contracts describe allowed messages and do not import a domain
that consumes them.
