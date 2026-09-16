# game2 socket protocol v1

Transport: TCP, default `127.0.0.1:8765`, one controller. All integers are
unsigned, network byte order (big endian). Each message is prefixed with a
4-byte `uint32` payload length, excluding the prefix. TCP reads/writes may be
partial or may contain several messages. Neither side assumes packet boundaries.
No JSON, pickle, arbitrary file paths, or direct world mutation is exposed.

`protocol.py` is the executable codec; `AgentClient` is the Python reference
adapter. A new connection receives a stream of frames without a handshake.
Only image pixels and timing/status metadata are observations.

## Controller → game

### Action: opcode 1, payload length 21

Python struct: `!BIIQHBB`.

| Order | Type | Field |
|---|---|---|
| 1 | u8 | opcode = 1 |
| 2 | u32 | sequence, strictly increasing per connection, starts at 1 |
| 3 | u32 | episode from the latest observation |
| 4 | u64 | target_tick, first simulation tick executing this action |
| 5 | u16 | hold_ticks, 1..120 |
| 6 | u8 | right, exactly 0 or 1 |
| 7 | u8 | jump, exactly 0 or 1 |

At receipt, `current_tick < target_tick <= current_tick + 120` is required.
Right holds on `[target_tick, target_tick + hold_ticks)`. Jump is a single edge
on target_tick. A new action overrides previous button holding from its start
until its own expiry; jump never repeats during holding. Of accepted commands
scheduled on the same tick, the last one wins. Commands may be scheduled on
several different future ticks. Reordering/replaying sequence numbers is rejected.
An accepted command may have no physical effect (jump while airborne, or a
terminal episode); accepted is a transport/scheduling acknowledgement.

### Reset: opcode 2, payload length 9

Python struct: `!BII`.

| Order | Type | Field |
|---|---|---|
| 1 | u8 | opcode = 2 |
| 2 | u32 | sequence, same counter as actions |
| 3 | u32 | expected current episode |

Reset takes effect at the next container input poll. It clears queued actions,
recreates physics, increments episode and sets tick to zero. The controller
must wait for the new episode before sending more actions. Other commands in
the same already-drained batch after reset are discarded. Sequence numbers
continue increasing across resets; reconnect starts a new sequence scope.

Payloads over 64 bytes or an overflowing 256-message inbox disconnect the peer.
Invalid opcode/fields, wrong episode and repeated sequence numbers increase
`rejected`; late actions increase `late`. Actions over 120 ticks ahead are
rejected. Incoming reset/action packets never block the physics loop.

## Game → controller

### Observation: opcode 128

Header struct: `!BBIQHHHHBIIIIIB` (44 bytes), followed by a zlib stream containing
exactly `width * height` index8 pixels. Payload length includes header and zlib
stream. Maximum accepted observation payload is 17 MiB.

| Order | Type | Field |
|---|---|---|
| 1 | u8 | opcode = 128 |
| 2 | u8 | protocol version = 1 |
| 3 | u32 | episode |
| 4 | u64 | completed physics tick |
| 5 | u16 | image width |
| 6 | u16 | image height |
| 7 | u16 | physics_hz, default 120 |
| 8 | u16 | monitor_hz, target 30 |
| 9 | u8 | status: 0 running, 1 dead, 2 success |
| 10 | u32 | accepted: last accepted command sequence, zero after connection |
| 11 | u32 | late command count |
| 12 | u32 | rejected command count |
| 13 | u32 | overrun_ticks: discarded wall time expressed in physical ticks |
| 14 | u32 | event_sequence, increments on die/success/reset |
| 15 | u8 | last_event: 0 none, 1 die, 2 success, 3 reset |

Coordinates: top left, row-major, y down. Index palette: 0 white `(255,255,255)`,
1 black `(0,0,0)`, 2 player blue `(0,102,255)`, 3 damage red `(255,0,0)`.
No HUD is encoded. Simulation time in seconds is `tick / physics_hz`.
No camera scaling/cropping is applied in v1.

Event sequence persists across resets. Last event repeats until a newer event.
Status is persistent. Frames continue after terminal status with the same tick;
reset is needed to start a new episode. Late/rejected counts last for the
container lifetime; overrun_ticks resets each episode. Accepted sequence resets
on connection change but not episode reset.

Target observation rate is 30 Hz, subject to host scheduling/render time. Tick
is authoritative: never infer elapsed simulation time from TCP arrival times or
frame count. Model inference runs independently. Controller must choose a
future target_tick with adequate latency margin and monitor late/overrun counts.

The game holds at most one in-flight and one latest pending observation. An
in-flight TCP message is completed, never truncated for a newer frame. Send
stall over 0.5 s disconnects the reader; old kernel-buffered frames can still
exist before disconnect. The reference AgentClient has a receiving thread that
continuously drains frames and retains only the latest unread observation.

Disconnect releases buttons and cancels queued actions at the next poll.
An inactive connected model is limited by hold_ticks. Simulation continues
without a peer while the episode is running. Closing the client does not close
the game; stop the game process/window to shut down the container and listener.
