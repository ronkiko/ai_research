# Display

The independent Console presentation boundary. Display consumes Engine STATE and
keeps internal frame diagnostics; it does not control Engine or receive Joystick
input. The future read-only `screen` and `vision` presentations are not
implemented by this patch.

Process entrypoint: `main.py`; runtime: `display.py`. Local documentation:
`doc/`.
