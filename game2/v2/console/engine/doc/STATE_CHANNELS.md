# State Channels

Engine publishes immutable multi-Actor STATE snapshots, world-level telemetry,
and actor-scoped events through Console-owned channels. STATE is consumed by
Display; telemetry supports Controller scheduling. Every channel that carries a
time value uses the canonical global `world_tick`; no `session_tick` or episode
clock is published. None of these channels gives a Player Engine internals.

STATE is `{session_id, world_tick, map, actors}` with deterministic Actor
ordering. Telemetry has the same world timestamp and one statistics entry per
Actor. Events include `actor_id` and `world_tick`; core runtime events do not
use `episode_started` or `episode_finished`.
