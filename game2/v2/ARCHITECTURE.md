# Game2 V2 Architecture

Status: modular runtime

```text
                          Management
                     /        |        \
                    /         |         \
          Screen Broker    Training      operator
               ^           composer         |
               |         /   |   |   \      |
        registered       C    T   M    P     |
        Screen #N        |         ^    |     |
               ^         |         |    |     |
               |       Console <- Joystick --+
               |         |
               +---- ScreenSource
                         |
                         +--------- Vision -> Player
```

Legend: C=Console process, T=Trainer, M=Model, P=Player.

## Runtime ownership

```text
Console        -> world, Engine clock, Controller, Vision, ScreenSource
Player         -> realtime perception/action timing
Model          -> inference, trajectories, updates, checkpoints
Trainer        -> episodes, reward, mastery metrics
Screen Server  -> numbered slot registry and source bindings only
Screen Window  -> one foreground native Pygame window
Management     -> process composition and binding
```

## Screen boundary

The graphical Screen is started independently:

```bash
./game2/v2/op/screen.sh 1
```

It registers itself with the background Screen Server and remains alive while
unbound. Training never creates or owns a graphical process.

With `--screen 1`, Management performs only:

```text
current Console ScreenSource
            |
            v
Screen Server BIND
            |
            v
already-open Screen #1
```

At map transition, UNBIND/BIND changes the source while the window remains the
same process. Closing the Screen cannot stop Console, Player, Model, or Trainer.

Screen Server never receives Engine STATE and never uses Player Vision as a
human-display shortcut; it only brokers the public rendered ScreenSource
capability.

## Timing

```text
world clock != player/model clock != training clock != screen clock
```

No UI, rendering, inference, Training, or Management operation may gate Engine
physics.
