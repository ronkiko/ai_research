# Scripted Player

The scripted process reads a public `PeripheralManifest`, connects to the
Joystick endpoint, sends version 1 decisions, and validates their ACKs. When the
manifest has `vision`, a separate reader receives public `VisionFrame` values and
keeps only the newest one. It does not know the Engine schedule, private command
protocol, or Engine STATE.
