# Capabilities

`console/config.py` contains private Session and subsystem manifests used only
for Console composition. The public `PeripheralManifest` is intentionally
defined in `contracts/manifests.py` and contains no Engine capability.

`DisplayManifest` is narrow but includes the STATE input, immutable `world_file`,
selected `mode` (`vision` or `screen`), and the optional public Vision bind
endpoint. It contains no CONTROL, TELEMETRY, EVENTS, Joystick, Player, or Trainer
capability. The public `PeripheralManifest` exposes only the Joystick and, for
Vision mode, the corresponding Vision endpoint.
