# Game2 V2 Architecture

Status: modular runtime

```text
                         Management
                    /        |        \
                   /         |         \
          Screen Server   Training      operator
               ^          composer
               |        /   |   |   \
          ScreenSource  C    T   M    P
               |       |         ^    |
               |       |         |    |
             Console <-+---- Joystick-+
               |                    ^
               +-------- Vision -----+
```

Legend: C=Console process, T=Trainer, M=Model, P=Player. Management owns process
composition only; it does not own their runtime objects or gameplay messages.

## Runtime ownership

```text
Console       -> world, Engine clock, Controller, Player peripherals, ScreenSource
Player        -> realtime perception/action timing
Model         -> inference, trajectories, updates, checkpoints
Trainer       -> episodes, reward, mastery metrics
Screen Server -> numbered native human windows
Management    -> start/stop/connect process boundaries
```

## Screen boundary

Console ScreenSource consumes private Engine STATE inside the Console domain and
publishes only already-rendered RGB `ScreenFrame` values.

Screen Server binds a numbered slot to a ScreenSource. It never receives Engine
STATE and never uses Player Vision as a human-display shortcut.

Closing a Screen window, stopping Screen Server, or losing Screen connectivity
must not stop Console, Player, Model, or Trainer.

## Training composition

Management composes the Training Set map-by-map:

```text
Console -> public Player attach
Trainer <-> Player training contract
Model   <-> Player model contract
Player  <-> Console Vision/Joystick
```

The default path is headless.

With `--screen N`, Management separately performs:

```text
Console ScreenSource -> Screen Server slot N
```

The Screen number never enters Console, Trainer, Model, Player, Vision, or
Joystick configuration.

## Timing

```text
world clock != player/model clock != training clock != screen clock
```

No UI, rendering, inference, Training, or Management operation may gate Engine
physics.
