# Console Configs

Data-only smoke configurations for the Console. Map paths are resolved relative
to each config file. `realtime-smoke.json` and `unpaced-smoke.json` exercise the
same fixed physics with different wall-clock pacing. The canonical realtime
smoke explicitly uses `display_mode: "vision"` so CI remains headless.
`screen-demo.json` is an explicit standalone human-facing Pygame opt-in.
`embedded-demo.json` keeps Engine STATE, TELEMETRY, and realtime pacing enabled
but disables the Console Display process; `demo.sh` supplies its private STATE
capability to an embedded ScreenRenderer.
