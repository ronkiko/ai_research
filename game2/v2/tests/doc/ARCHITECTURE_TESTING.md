# Architecture Testing

Tests verify both filesystem structure and source import boundaries. They also
retain fixed-world, Joystick, ACK, transport, lifecycle, realtime, and unpaced
behavior checks so relocation cannot silently change runtime semantics.
