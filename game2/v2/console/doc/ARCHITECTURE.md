# Console Architecture

`main.py` composes and supervises the Console-owned Engine, Controller, and
optional Display processes. The hot path does not pass through the composition
process after startup. `console/world/` is an in-process static domain, not a
separate process.

The Console is an MMO-like authoritative server for one shared World, with
zero or many Players and one global `world_tick`. Patch 2 implements the
internal multi-Actor runtime; the temporary external workflow still starts one
explicit compatibility Player/Actor. See the normative model in
[MMO_SERVER_MODEL.md](MMO_SERVER_MODEL.md).

World owns the immutable tile-authored scene definition. Engine owns the live
multi-Actor world state and one shared hot-path Physics object built from World
collision geometry. Controller owns Joystick ingress and private actor-scoped
command translation, and Display consumes authoritative multi-Actor STATE.
Engine owns the single global `world_tick`, not a Player or a Training episode.
Private manifests are kept in `config.py`; public Player capabilities come from
`contracts/`.

Actors are stepped in sorted `actor_id` order. A terminal Actor is frozen
locally; other Actors and the global World clock continue. The compatibility
Console path explicitly binds one Player ID to a distinct Actor ID after Engine
construction. It is not a global Avatar.
