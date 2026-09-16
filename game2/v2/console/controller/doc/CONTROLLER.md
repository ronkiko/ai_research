# Controller Boundary

Controller converts complete public Joystick decisions into private
`ActionCommand` messages. Engine ACK statuses are mapped back to the public
Joystick ACK statuses. Player sequence numbers remain visible only at the
public boundary; target ticks remain private.
