# Public Contracts

`joystick.py` defines the version 1 two-button digital decision and its ACK
shape. `vision.py` defines the public semantic Vision frame: a small versioned
JSON header followed by raw `u8-semantic` bytes. `manifests.py` defines `Endpoint`
and the Player-facing `PeripheralManifest`, whose capabilities are `session_id`,
`joystick`, and optional `vision`.
`framing.py` defines generic length-prefixed JSON framing and protocol version
validation. Vision pixels deliberately do not pass through JSON or Base64.

Engine CONTROL commands, target ticks, hold ticks, and other scheduling details
are Console-private and do not belong here.
