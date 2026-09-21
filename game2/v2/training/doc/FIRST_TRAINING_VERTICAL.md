# Current Training Vertical

Status: executable Game2 V2 training contract.

## One Data Path

Realtime and unpaced execution have one learning boundary:

```text
public Vision + public Joystick/lifecycle evidence
                    |
                    v
             EpisodeDataset
                    |
                    v
        canonical dataset PPO trainer
                    |
                    v
              checkpoints
```

Execution speed is not a training semantic. Realtime follows wall-clock pacing;
unpaced advances the same fixed-step Engine as fast as possible. Both emit the
same episode row schema and use the same `training/work/ppo.py` update.

## EpisodeDataset

One episode is one scalar/text SQLite file under the episode store plus its
matching compressed Vision sidecar. Together they are the training source of
truth and universal training log. The main database stores:

- episode id, mode, source, seed, result, finish tick and progress;
- references needed to recover the exact public Vision matrices for each
  policy decision from the matching Vision sidecar;
- policy world tick and duration in world ticks;
- motion and current virtual-pad state;
- MotorGoal, action/toggle decision, desired pad state and policy probability;
- SELF/GOAL positions derived from public Vision;
- actuation acknowledgement state;
- reward, GAE, normalized advantage and return;
- PPO-selection flag and post-update value/log-probability/ratio;
- aggregate PPO/update metrics.

The store retains at most the five newest main/sidecar episode pairs.
`--fresh` clears both stores before collection starts.

There is no parallel trajectory JSONL, in-memory replay record type, or separate
realtime/unpaced PPO implementation.

## Policy, Planner And Physics Cadence

Console physics stays at 120 Hz. Current Motor policy cadence targets one
decision every two world ticks (60 Hz). Planner is intentionally slower: one
Planner opportunity every 12 world ticks (10 Hz). Planner commands a persistent
MotorPlan; between Planner decisions the Motors keep closing the physical
feedback loop using the latched MotorGoal and current Proprioception.

Realtime wall-clock delivery is not lockstep. Player/Model IPC uses a
latest-observation mailbox: when inference falls behind, intermediate
observations may be replaced by newer observations instead of accumulating a
stale FIFO. This is intentional backpressure. It preserves freshness but does
not redefine the target Motor cadence; large observed world-tick gaps between
Motor decisions are a performance/control issue to measure separately.

Discounting uses actual `world_tick` gaps and is scaled in Planner-time
(`PPO_DISCOUNT_TICKS = 12`), so irregular wall-clock delivery is represented
explicitly rather than pretending every record is equally spaced.

## Realtime Actuation Identity

A completed model decision is a causal object with a stable `decision_id`.
The realtime actuator contract is:

```text
new decision
 -> one Controller request at most
 -> CONTROL_REQUESTED
 -> Controller ACK
 -> CONTROL_RESULT
 -> ACTUATED only when ACK == accepted
```

No-op KEEP decisions are valid policy choices even when they generate no wire
request. State-changing realtime rows become PPO-eligible only when their
execution evidence satisfies the actuation filter. All realtime Player paths,
including Training, must use this same request/result/actuation semantics.

## PPO Selection And Update Boundary

Reward and GAE are calculated inside one completed episode and never cross an
episode boundary. Under the current configuration
`PPO_HISTORY_STRIDE_TICKS == POLICY_STRIDE_TICKS == 2`, so normal eligible
Motor decisions are not intentionally sparsified to a 10-tick history cadence.
The terminal-tail rule remains, but the historical "one early row per 10 ticks"
description no longer applies.

Realtime eligibility filtering and PPO sampling are separate from causal plan
replay. A Planner row may be excluded as a policy sample while still being the
saved source of a latched MotorPlan used by later eligible Motor rows. PPO may
replay that source row only as context; it must not assign reward/advantage or
policy loss to an otherwise ineligible source.

The current online update boundary is one completed trainable episode -> one PPO
update -> next episode. Multi-episode PPO accumulation is not part of the
current executable contract.

## Process Ownership

Console owns physics and lifecycle. Player owns public Vision, public Joystick
and actuator timing. Model runtime owns learned inference and weights.
EpisodeDataset owns collected training material. The canonical PPO trainer owns
the gradient update. Training/Management orchestrate episodes and checkpoints.

No training component reads private Engine state or calls an Engine step
through a Trainer-facing API.


## Reward And Mastery

Training task reward is dense progress plus terminal semantics:

```text
success terminal = +1
death terminal   = -1
timeout terminal = 0
```

Timeout still fails qualification. The zero terminal value is deliberate: an
earlier `timeout=-1` erased useful partial-progress credit and taught the
Critic/policy to dislike progressed late states.

A map is mastered only after three consecutive deterministic learning-OFF
successes. The final frozen Training Set check uses the same 3-in-a-row rule.


## Multi-rate realtime/unpaced parity

Realtime and unpaced share the same current sensor semantics: 120 Hz physics,
60 Hz Motor opportunities, 30 Hz Vision capture, and 10 Hz Planner
opportunities. Unpaced may advance those clocks without sleeping, but it does
not render a fresh camera frame on every Motor decision. Intermediate Motor
decisions reuse the latest captured Vision frame with fresh Proprioception.

EpisodeDataset schema v14 records the Motor/Proprioception decision tick in the
main step and the exact Vision capture tick in the Vision sidecar. PPO replay
therefore reconstructs the same asynchronous sensor pair used online.
