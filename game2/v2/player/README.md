# Player

The external decision-maker domain. Player implementations receive public
Vision observations, choose Joystick decisions, and may later host model
runtimes.

Player owns no world, physics, Engine command, Console manifest, or management
runtime. It may import only `contracts/*` and its own modules.

Current implementations: `scripted/main.py` and `human/main.py`. The Human
Keyboard Player is an external process that reads only `PeripheralManifest` and
uses only the public Joystick. Scripted Player may additionally subscribe to
public Vision:

```text
Human Keyboard Player -> public Joystick -> Console Controller -> Engine
Scripted Player -> public Vision
Scripted Player -> public Joystick -> Console Controller -> Engine
```

Human Player has no Engine access. Local documentation: `doc/`.
