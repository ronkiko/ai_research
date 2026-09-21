# Public Contracts

The `contracts` package is the architectural leaf for public cross-domain
capabilities. Its current Player-facing surface is:

- `joystick.py` - Player action decisions and public action ACKs.
- `vision.py` - public semantic Vision frames.
- `proprioception.py` - public physical self-body measurements only.
- `manifests.py` - `Endpoint`, the compatibility `PeripheralManifest`, and the
  current dynamic `PlayerManifest`.
- `connection.py` - public Player lifecycle: `PROBE`, `ATTACH`, `START`,
  `RESPAWN`, and `DETACH`, plus lifecycle ACKs and the terminal Player event.
- `discovery.py` - public discovery of the running Console.
- `framing.py` - generic length-prefixed framing and protocol versioning
  utility.

Vision physics and metadata matrices deliberately do not pass through JSON or Base64. The following
remain Console-private and are not public Player contracts:

- Engine CONTROL;
- Engine STATE;
- Engine TELEMETRY;
- private Engine EVENTS;
- private `InputStateCommand`;
- Engine input-latch state;
- any future timing or macro mechanism.


## Bot Profile

`bot_profile.py` defines the versioned machine-readable configuration of one
learned Bot. A Bot is a Management/AI configuration concept only: when it enters
the Console it uses its `bot_id` as an ordinary public `player_id` and receives
no special Console capability.

A Bot Profile describes the future cerebral cortex / Research Strategist, the
spinal-cord Planner, and a named list of reflex Motors. It also carries
UI-readable implementation, configuration, precision, seed, and topology
metadata so the future Bot Profiler does not need to inspect PyTorch classes.

## Proprioception Reality Boundary

Proprioception is a public Player capability, but it is deliberately narrower than private Engine TELEMETRY.
The admission rule is physical:

> A Proprioception value is allowed only if a real humanoid could measure the corresponding property of its own body with a contemporary instrument or onboard sensor.

The current v1 frame contains only measured linear velocity vx/vy, ground/contact state, and the measured states of the Player's own RIGHT and JUMP actuators. world_tick and session_id are synchronization/protocol metadata, not extra senses.

Explicitly outside Proprioception are map identity or geometry, level/world limits, finish/goal information, other actors or enemies, hazards, future collisions, predicted future state, hidden collision queries, Controller statistics, simulation speed, and lifecycle/result semantics.

Absolute simulator coordinates are also excluded from v1. A future embodiment may add an equivalent only if it explicitly models a real localization instrument and its measurement characteristics.

External-world sensing belongs to explicit exteroceptive capabilities such as Vision, and later physically modelled lidar/radar/audio if such sensors are intentionally added. It must never be smuggled through Proprioception.

Console may read private multi-Actor Engine TELEMETRY to implement the sensor, but ProprioceptionSource is a one-way capability filter: it selects the attached Player's own Actor and emits only the strict public frame. Raw TELEMETRY never becomes a Player capability.
