# Controller Boundary

Controller is the realtime bridge between the public digital Joystick and the
private Engine input latch.

Each public `JoystickState` is a complete snapshot:

```text
RIGHT A
0     0   neutral
1     0   hold right
0     1   hold A
1     1   hold right + A
```

For a new valid Player sequence, Controller immediately sends one private
`InputStateCommand(actor_id, sequence, right, jump)` to Engine. There is no
`target_world_tick`, `hold_ticks`, lead time, future queue, or macro program.

Engine acknowledgement statuses map directly to public `accepted`,
`duplicate`, and `rejected` statuses. Public Player sequence numbers remain
at the Joystick boundary; Controller uses its own monotonic private sequence for
Engine CONTROL.

If the active Joystick connection disappears, Controller sends a neutral
`RIGHT=0, A=0` state before closing its Engine CONTROL connection. Engine also
neutralizes input at lifecycle boundaries.
