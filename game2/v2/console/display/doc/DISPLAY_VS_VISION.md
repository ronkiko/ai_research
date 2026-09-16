# Display Versus Vision

Display is a Console presentation consumer. Future `Display.screen` and
`Display.vision` interfaces will be two read-only presentations of
`WorldDefinition` and `WorldState`: artwork for humans and semantic world
representation for models. They are not implemented yet.

A future VisionAdapter will be a separate Player-facing sensory contract that
may consume the semantic vision presentation. It must not turn Display into a
privileged Engine-state API, and vision is not raw x/y/vx/vy telemetry.
