# Scripted Player

Deterministic external Player used by the integration smoke and Vision demo. It
sends public Joystick decisions and reads the optional public Vision stream in a
separate latest-frame receiver loop. Slow Vision processing never controls the
Engine clock.

It has no Console implementation dependency. Entrypoint: `main.py`. Local
documentation: `doc/`.
