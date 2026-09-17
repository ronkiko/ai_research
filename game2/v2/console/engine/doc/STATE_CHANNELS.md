# State Channels

Engine publishes immutable STATE snapshots, telemetry, and events through
Console-owned channels. STATE is consumed by Display; telemetry supports
Controller scheduling. Every channel that carries a time value uses the
canonical global `world_tick`; no `session_tick` or episode clock is published.
None of these channels gives a Player Engine internals.
