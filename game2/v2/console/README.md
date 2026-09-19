# Console

The virtual game console and its owned runtime subsystems. Console owns the
authoritative World, Engine, Controller, internal transport, private protocols,
and Player-facing peripheral production. It does not own Player, Model, Trainer,
Management, or the operator Screen Server.

Its internal gameplay decomposition is:

```text
Console
├── Engine    authoritative mutable runtime and fixed-step world clock
├── World     immutable tile-authored scene definition
├── Controller
└── Display
```

`WorldDefinition` is loaded before Engine starts. Engine materializes one shared
physics/rules object and independent `ActorBody` values from that definition.
Display loads the same immutable world resource independently and combines it
with latest Engine STATE for read-only presentation. Physics remains an Engine
hot-path component, not a separate process.

The canonical runtime is the persistent zero-player server started by
`game2/v2/boot.sh`. External Players attach later and receive narrow public
Joystick/Vision capabilities.

The old embedded graphical demo shell was removed. Human observation is not a
Console lifecycle mode; it will be attached through the independent Screen
Server composition path.

The target MMO-like Console model is normative in
[`doc/MMO_SERVER_MODEL.md`](doc/MMO_SERVER_MODEL.md). Console may import public
`contracts/*`; it must not import external runtime domains.

Entrypoint: `main.py`. Normative contract: `SPEC.md`. Local documentation:
`doc/`.
