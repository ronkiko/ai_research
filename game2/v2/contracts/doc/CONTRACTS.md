# Public Contracts

`joystick.py` defines the version 1 two-button digital decision and its ACK
shape. `manifests.py` defines `Endpoint` and the Player-facing
`PeripheralManifest`, whose only capabilities are `session_id` and `joystick`.
`framing.py` defines generic length-prefixed JSON framing and protocol version
validation.

Engine CONTROL commands, target ticks, hold ticks, and other scheduling details
are Console-private and do not belong here.
