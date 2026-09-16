# Console

The virtual game console and its owned processes. Console owns composition,
lifecycle, Engine, Controller, Display, internal transport, private protocols,
manifests, and configs.

Console does not own Player, model, Trainer, or management UI. It may import
public `contracts/*`; it must not import the other external domains.

Entrypoint: `main.py`. Normative contract: `SPEC.md`. Local documentation:
`doc/`.
