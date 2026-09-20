# Public Contracts

The `contracts` package is the architectural leaf for public cross-domain
capabilities. Its current Player-facing surface is:

- `joystick.py` - Player action decisions and public action ACKs.
- `vision.py` - public semantic Vision frames.
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
