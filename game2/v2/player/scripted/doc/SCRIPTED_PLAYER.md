# Scripted Player

The scripted process reads a public `PeripheralManifest`, connects to the
Joystick endpoint, sends version 1 decisions, and validates their ACKs. It does
not know the Engine schedule or private command protocol.
