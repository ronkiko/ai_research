# Organism

`organism/` is the canonical learned-body core extracted from GameLab in
refactor stage 04. It owns controller code, declared sensors, Spine/Motor models,
Motor packages and certificates, Motor/Spine schools, verification, experiment
job primitives and artifact validation.

GameLab remains only a compatibility/history facade. After cutover 09 the
active VN and public learned-body operator path use `organism/` directly;
there is not a second control loop or trainer.

## Boundaries

- Physics authority remains GameServer.
- Live transport/session authority remains GameClient Host.
- `control_loop` emits only normalized Motor effort through Host.
- Sensors contain declared self state and goal channel only; renderer/world truth
  is not a policy input.
- Motor receives local MotorGoal + proprioception, never strategic target_x.
- `SemanticGoalAdapter` resolves map/object meaning only to a local x region;
  it never supplies speed, braking, effort or trajectory.
- `BodyController` is a long-lived single-writer worker. A caller or LLM
  session ending does not end its active goal.
- A zone change clears incompatible sensor history. A goal revision in the same
  zone preserves measured history and changes only the goal channel.
- Unpaced training still drives the same canonical `ZoneRuntime`, whose motion
  math is the shared `gameserver.v1.physics` kernel.

## Deployment

Organism is **client-side relative to GameServer**. It does not have to run on
the GameServer VPS or on the same machine as a human player.

Typical Yuki node:

```text
LLM → Organism → Spine → Motor → Host[yuki]
                                  │
                                  └─ network → GameServer Gateway
```

This allows Yuki to live on a GPU workstation/VPS while GameServer runs
elsewhere. `Host[yuki]` stays local to Organism and is the only gameplay-facing
transport used by the learned body. Organism must not bypass Host to call World
or Physics services directly.

A failure or restart of the human Player Gateway is therefore independent of
Yuki's control node, and a failure of Yuki's compute node does not stop the
authoritative world.

See [distributed runtime](../docs/deployment/distributed-runtime.md).

## Artifacts

New Motor instances live under `organism/motors/instances/`. The registry can
import an old certified GameLab Motor only after validating its manifest,
socket/physics contract, source hashes and certified brain hash, then copies the
bytes atomically. Invalid or uncertified legacy instances are not mounted.

The default Spine checkpoint/reward paths now live under `organism/runtime/`.
A pre-refactor Spine checkpoint can be imported only after loading it against
the certified Motor identity/hash; the checkpoint bytes are copied unchanged.
No import retrains a model.

The public `learning_v1` MCP was implemented in stage 08; stage 09 switched
GameTable to `navigation_v1 + learning_v1` and added the Organism operator
launcher.

## Operator

~~~bash
./organism/op/organism.sh check
./organism/op/organism.sh train motor
./organism/op/organism.sh train spine
./organism/op/organism.sh verify
./organism/op/organism.sh serve learning
~~~

The private `organism/.venv` is bootstrapped by this launcher. GameTable uses
the same launcher for both active MCP services.

## learning_v1

`organism/mcp.py` exposes named Motor/Spine curricula, preparation, asynchronous
training/status/cancel, frozen verification and explicit skill selection.

The interface is ID-only. Storage paths and arbitrary code are private. Physical
learning holds the same `BodyLease` used by navigation. Motor certification is
one-shot. Spine candidates never replace the mounted production binding merely
because training changed weights.

`training_prepare` is the only setup bootstrap and requires a server-issued
Director authorization when the body is not already in `training/flat_run`.
Its receipt is assisted setup, not learned locomotion evidence.
