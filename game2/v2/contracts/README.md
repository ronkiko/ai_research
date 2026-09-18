# Contracts

Public, versioned agreements shared by independent V2 domains.

Contracts own the Player-facing Joystick, Vision, dynamic PlayerManifest,
Console connection lifecycle messages, local discovery schema, generic wire
framing, and the executable Trainer/learned Player control plane in
`training.py`. They do not own Engine commands, physics, scheduling, or runtime
objects. Contracts import no V2 domain.

Each contract validates exact fields, protocol version, value types, and finite
numeric values before a message crosses a process boundary. Entrypoints: none.
Local documentation: `doc/`.
