# Capabilities

`console/config.py` contains private Session and subsystem manifests used only
for Console composition. The public `PeripheralManifest` is intentionally
defined in `contracts/manifests.py` and contains no Engine capability.

`DisplayManifest` is narrow but includes the three things Display needs:
`engine_state`, the immutable `world_file`, and `mode` (`vision` or `screen`). It
contains no CONTROL, TELEMETRY, EVENTS, Joystick, Player, or Trainer capability.
