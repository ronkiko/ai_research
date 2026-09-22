# GameLab v1 Specification

Status: first hierarchical learned-control experiment.

## Hypothesis

A slow strategic model should be able to issue a durable physical goal while a
faster learned hierarchy performs continuous feedback control without further
strategic inference.

The initial task is one-dimensional target positioning.

## External environment

GameLab uses the public GameClient Host Protocol at `127.0.0.1:17700`.
GameClient Host remains the only GameServer-facing client.

GameLab must not import implementation code from `gameclient.*` or
`gameserver.*`.

The external authoritative world remains:

- physics: 120 Hz;
- world x interval: 0..1000;
- player speed: 180 units/s;
- movement intent: -1, 0, +1 and latched by GameServer.

## Learned hierarchy

### Spine

Spine is a temporal CNN evaluated at 10 Hz.

Input shape:

```text
4 channels x 32 history frames
```

Channels are normalized self position, self velocity, current actuator state,
and strategic target displacement.

Output is a learned 4-dimensional continuous MotorGoal. Its semantics are not
hand-authored.

### Motor 1

Motor 1 is an MLP evaluated at 60 Hz.

Input is:

```text
MotorGoal[4] + proprioception[vx, current_move]
```

Output is three logits corresponding to:

```text
LEFT / STOP / RIGHT
```

Motor 1 does not receive target position or target displacement directly.

## Learning

Spine, Motor, and critic are optimized jointly with PPO. Training action labels
must not come from a scripted controller.

Reward and success measurement may use authoritative state because they belong
to the training laboratory, not the deployed controller.

Default dense reward measures reduction in absolute target distance. Terminal
success adds positive reward. Timeout adds negative terminal reward.

## Success

The learned policy succeeds only when:

- target error is within configured tolerance;
- measured velocity is zero;
- latched movement intent is zero;
- that state remains stable for the configured hold period.

A post-terminal safety stop is not part of success classification.

## Strategic interface

The LLM-facing GameLab MCP exposes goals, not actuator commands:

```text
health
model_info
set_goal
goal_status
cancel_goal
```

The LLM is never required to issue actions at Motor cadence.

## Verification

VERIFY uses the saved checkpoint with learning disabled and greedy Motor action
selection.

No heuristic, teacher, PID, scripted trajectory, or procedural correction may
participate in VERIFY.

## Future second Motor

A later experiment may add Motor 2. The intended research question is learned
coordination under a common Spine, not procedural alternation. No gait scheduler
is reserved in v1.
