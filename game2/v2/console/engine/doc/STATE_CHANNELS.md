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

## Player Proprioception Projection

Engine TELEMETRY remains Console-private and multi-Actor. It contains fields that must never be handed directly to a Player, including Actor identities, input statistics, lifecycle/result information, and simulation-speed metadata.

A Console-owned ProprioceptionSource may subscribe to TELEMETRY only as an implementation detail. It selects one Player-owned Actor and projects a strict public frame of physically measurable self-body values. This is a capability reduction, not a public TELEMETRY channel.

The public projection currently excludes coordinates, world/map information, other Actors, result state, counters, and prediction. Future additions require a real-hardware sensor analogue.
