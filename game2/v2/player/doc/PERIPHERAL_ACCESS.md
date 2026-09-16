# Peripheral Access

Player receives `PeripheralManifest` and uses its Joystick endpoint. It does not
receive Engine CONTROL, STATE, TELEMETRY, EVENTS, private manifests, target
ticks, hold ticks, or mutable world state.

The Human Keyboard Player is one external implementation of this boundary. Its
keyboard window produces held `right` and `jump` state; it never sends private
Engine commands.
