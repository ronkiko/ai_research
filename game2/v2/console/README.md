# Console

The virtual game console and its owned processes. Console owns composition,
lifecycle, Engine, World, Controller, Display, internal transport, private
protocols, manifests, and configs.

Its internal gameplay decomposition is:

```text
Console
├── Engine    authoritative mutable runtime and fixed-step clock
├── World     immutable tile-authored scene definition
├── Controller
└── Display
```

`WorldDefinition` is loaded before Engine starts. Engine materializes its
mutable `AvatarBody` and physics surfaces from that definition. Display loads
the same immutable world resource independently and combines it with the latest
Engine STATE for its `screen` or `vision` presentation. Physics remains an
Engine hot-path component, not a separate process. World contains no runtime
state and has no dependency on Engine or the physics implementation.

Console does not own Player, model, Trainer, or management UI. It may import
public `contracts/*`; it must not import the other external domains.

The temporary embedded demo runs Console with `enable_state: true` and
`enable_display: false`. Console can write a private operator-only STATE
capability for that host; it remains separate from the public Player
`PeripheralManifest`.

Entrypoint: `main.py`. Normative contract: `SPEC.md`. Local documentation:
`doc/`.
