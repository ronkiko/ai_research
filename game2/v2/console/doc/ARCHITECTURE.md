# Console Architecture

`main.py` composes and supervises the Console-owned Engine, Controller, and
optional Display processes. The hot path does not pass through the composition
process after startup. `console/world/` is an in-process static domain, not a
separate process.

World owns the immutable tile-authored scene definition. Engine owns the live
world state and converts World collision geometry to hot-path Physics objects.
Controller owns Joystick ingress and private command translation, and Display
consumes authoritative STATE. Private manifests are kept in `config.py`; public
Player capabilities come from `contracts/`.
