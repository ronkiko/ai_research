# Realtime Input State

Game2 V2 Controller performs **no gameplay input scheduling**.

The old finite-hold model (`target_world_tick`, `hold_ticks`, controller
lead ticks, future command queues) is removed from the normal gameplay path.

The canonical semantics are physical-controller semantics:

```text
Player changes pad -> Controller forwards current state -> Engine latches state
Engine physics tick -> reads the currently latched state
```

If RIGHT becomes true and no later state arrives, RIGHT remains held. If RIGHT
becomes false, it is released. Engine never waits for another Player decision.

A is also a held button state, but gameplay interprets the rising edge
`A: 0 -> 1` as one jump press. Keeping A at 1 cannot auto-repeat jumps.
A must return to 0 before a later `0 -> 1` can create another jump.

Future scripted/replay tools may generate timed Joystick changes externally,
but Controller itself must never accept or store a future program of button
presses.
