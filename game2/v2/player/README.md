# Player

The external decision-maker domain. Player implementations choose Joystick
decisions and may later host model runtimes.

Player owns no world, physics, Engine command, Console manifest, or management
runtime. It may import only `contracts/*` and its own modules.

Current implementation: `scripted/main.py`. Local documentation: `doc/`.
