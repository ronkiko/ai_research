# Display

Display is the independent Console presentation domain. It loads the immutable
`WorldDefinition`, consumes the Engine's latest STATE snapshots, and owns two
read-only presentations of the same authoritative world:

```text
WorldDefinition + latest WorldState
                 |
              Display
              /     \
          screen    vision
          human     semantic
```

`screen/` is the optional Pygame human view. `vision/` is a deterministic,
headless semantic raster and the source for the public Vision peripheral. Neither
presentation controls Engine, receives Joystick input, reads TELEMETRY/EVENTS, or
changes World/Physics. Each public Vision subscriber has a single newest-frame
slot, so a slow examiner cannot block the Engine or another subscriber.

`DisplayService` exposes the same latest-state reader as `start()`,
`present_latest()`, and `close()` for the temporary embedded shell; `run()`
remains the standalone process wrapper.

Process entrypoint: `main.py`; runtime: `display.py`. Local documentation is in
`doc/`, `screen/`, and `vision/`.
