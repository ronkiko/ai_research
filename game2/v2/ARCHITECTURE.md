# Game2 V2 Architecture

Status: modular runtime

```text
                    Management
                        |
          +-------------+-------------+
          |                           |
   Screen Server                  experiment composition
          ^
          |
     ScreenSource
          |
       Console <---- Joystick ---- Player <----> Model
          |                          ^
          +------ Vision ------------+
                                     |
                                  Trainer
```

The arrows are contracts, not object ownership across domains.

## Screen boundary

Console owns a headless `ScreenSource` presentation plugin. It consumes private
Engine STATE inside the Console domain and emits only already-rendered RGB
`ScreenFrame` values through the public read-only Screen contract.

Screen Server is independent Management infrastructure. A numbered Screen slot
can bind to any `ScreenSourceDiscovery`. Each bound slot owns only its native
window process. Closing the window or stopping Screen Server cannot stop Console,
Player, Model, or Trainer.

Screen Server must not consume Player Vision as a human-display shortcut and
must never receive Engine STATE.

## Machine boundary

Vision is headless and machine-facing. Player observes Vision and acts through
Joystick. Screen is not an input to Player and is not part of Training data.

## Timing

```text
world clock != player/model clock != training clock != screen clock
```

No presentation, inference, training, or management process may block Engine
physics.

## Composition

The previous all-owning Training/GUI launcher remains removed. Management may
compose independent processes only through executable contracts. The next
Training patch can add optional Screen-slot binding, but absence or failure of
Screen must not alter Training behavior.
