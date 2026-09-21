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

Realtime pairing is causal: a Vision observation may use only the newest Proprioception frame whose world_tick is less than or equal to the Vision world_tick. A future sensor frame must never influence an earlier Vision decision.

This capability is designed for eventual hardware transfer. A new field requires a concrete real-world measurement story such as IMU, encoder, contact switch, force/load sensor, or actuator feedback. Semantic knowledge about the world is not a body sensor.
