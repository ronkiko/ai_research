# Architecture Testing

Tests verify both filesystem structure and source import boundaries. They also
retain fixed-world, Joystick, ACK, transport, lifecycle, realtime, and unpaced
behavior checks so relocation cannot silently change runtime semantics.

Architecture tests protect two kinds of boundaries:

1. Domain/import boundaries: absolute and relative imports must obey the rules
   in `doc/DEPENDENCY_RULES.md`.
2. Realtime semantic boundaries: world timing must remain independent of Player,
   model, Training, Display, rendering, and UI timing.

Realtime regressions include an Engine that progresses without a Player, a slow
Player that does not stop the Engine, Display work that does not affect physics,
UI absence that does not affect gameplay, and `realtime`/`unpaced` execution
that preserves world semantics.
