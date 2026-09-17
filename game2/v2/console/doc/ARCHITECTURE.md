# Console Architecture

`main.py` composes and supervises the Console-owned Engine, Controller, and
optional Display processes. The hot path does not pass through the composition
process after startup. `console/world/` is an in-process static domain, not a
separate process.

The target Console is an MMO-like authoritative server for one shared World,
with zero or many Players and one global `world_tick`. The current runtime is
still a transitional single-player vertical: its singleton Avatar and Episode
areas are migration debt to be removed in Patch 2/3. See the normative target
model in [MMO_SERVER_MODEL.md](MMO_SERVER_MODEL.md).

World owns the immutable tile-authored scene definition. Engine owns the live
world state and converts World collision geometry to hot-path Physics objects.
Controller owns Joystick ingress and private command translation, and Display
consumes authoritative STATE. Engine owns the single global `world_tick`, not a
Player or a Training episode. Private manifests are kept in `config.py`; public
Player capabilities come from `contracts/`.
