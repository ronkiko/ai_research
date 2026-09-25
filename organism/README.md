# Organism

`organism/` is the canonical learned-body core extracted from GameLab in
refactor stage 04. It owns controller code, declared sensors, Spine/Motor models,
Motor packages and certificates, Motor/Spine schools, verification, experiment
job primitives and artifact validation.

GameLab remains the compatibility service and current public operator shell until
the later cutover. Its old core module paths are thin imports of this package;
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

## Artifacts

New Motor instances live under `organism/motors/instances/`. The registry can
import an old certified GameLab Motor only after validating its manifest,
socket/physics contract, source hashes and certified brain hash, then copies the
bytes atomically. Invalid or uncertified legacy instances are not mounted.

The default Spine checkpoint/reward paths now live under `organism/runtime/`.
A pre-refactor Spine checkpoint can be imported only after loading it against
the certified Motor identity/hash; the checkpoint bytes are copied unchanged.
No import retrains a model.

The final operator launcher and public `learning_v1` MCP are intentionally
deferred to stages 09 and 08 respectively.

## Check

The current compatibility gate runs both organism and GameLab tests:

~~~bash
./gamelab/op/gamelab.sh check
~~~
