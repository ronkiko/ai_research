# Clock

Engine owns one monotonically increasing physical clock, `world_tick`, and
advances one fixed physics opportunity per world tick. Realtime pacing affects
wall-clock sleeping only; unpaced execution advances the same fixed physics
step without waiting for a Player decision. Terminal compatibility actors do
not stop `world_tick`.
