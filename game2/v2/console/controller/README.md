# Controller

The Console input subsystem. Controller owns the public Joystick listener and is
the only gameplay client of Engine CONTROL.

Its job is intentionally small:

```text
JoystickState {RIGHT, A}
        |
        v
Controller
        |
        v
InputStateCommand {RIGHT, A}
        |
        v
Engine input latch
```

Controller does **not** schedule future input, choose a duration, or invent a
release time. It forwards the newest complete button state immediately and maps
the Engine acknowledgement back to the public Joystick acknowledgement.

A held button remains held because Engine stores the current virtual-pad state,
not because Controller repeatedly schedules finite commands.

Controller has no TELEMETRY dependency. It does not own Player intelligence,
World, Physics, Training, or rendering.
