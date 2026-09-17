# Human Player

The process receives a public `PeripheralManifest` and uses only its Joystick
endpoint. Every input cycle sends a complete `JoystickState` through
`joystick_message`; button state is not represented as one-shot key changes.

ACK reading runs in a separate thread. `accepted`, `duplicate`, and `rejected`
ACKs are diagnostics and never become an input clock or a retry instruction.
Each sent state gets a strictly increasing session sequence. Transport
disconnects and malformed ACK streams are real Player failures.

The standalone Pygame window is only an input device boundary. The reusable
`HumanKeyboardInput` adapter has no window or event-loop ownership; an embedded
host passes it KEYDOWN/KEYUP and focus events. Focus loss clears all held keys to
neutral. Closing a standalone window does not send Engine commands, reset the
episode, or pause the world; the Console Engine continues with its neutral
fallback after the Player disconnects.

The Player cannot access `Engine`, `Controller`, `Display`, `World`,
`ActionCommand`, scheduling fields, telemetry, or private manifests.
