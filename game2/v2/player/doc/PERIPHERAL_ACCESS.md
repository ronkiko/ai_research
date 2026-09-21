# Peripheral Access

Player receives `PeripheralManifest` and uses its Joystick endpoint. It does not
receive Engine CONTROL, STATE, TELEMETRY, EVENTS, private manifests, target
ticks, hold ticks, or mutable world state.

The Human Keyboard Player is one external implementation of this boundary. Its
keyboard window produces held `right` and `jump` state; it never sends private
Engine commands.

## Proprioception

A current dynamic Player receives a dedicated proprioception endpoint. It is not Engine TELEMETRY.
Console filters private TELEMETRY into a strict self-body frame containing only: vx, vy, grounded, right_pressed, and jump_pressed.

Realtime sensing is causal but intentionally multi-rate. Vision keeps its own
capture `world_tick`; the current public camera runs at 30 Hz. Motor decisions
are clocked by fresh Proprioception at 60 Hz, so a decision may legitimately use
the latest camera frame from an earlier tick together with a newer self-body
measurement. This is normal asynchronous sensor fusion, not simulator
foresight. A Vision frame captured after the Proprioception/decision tick is
forbidden.

The EpisodeDataset preserves both times: the policy step `world_tick` is the
Motor/Proprioception decision tick, while the Vision sidecar stores the exact
capture tick of the frame actually used.

This capability is designed for eventual hardware transfer. A new field requires a concrete real-world measurement story such as IMU, encoder, contact switch, force/load sensor, or actuator feedback. Semantic knowledge about the world is not a body sensor.
