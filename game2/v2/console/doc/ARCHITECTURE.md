# Console Architecture

`main.py` composes and supervises the Console-owned Engine, Controller, and
optional Display processes. The hot path does not pass through the composition
process after startup.

Engine owns the world, Controller owns Joystick ingress and private command
translation, and Display consumes authoritative STATE. Private manifests are
kept in `config.py`; public Player capabilities come from `contracts/`.
