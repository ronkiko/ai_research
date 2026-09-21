# Trainer, Dataset, And Model Boundary

The current Game2 V2 learning boundary is dataset-driven.

The realtime Player/Model path and the unpaced runner may collect an episode at
different wall-clock rates, but both persist the same `EpisodeDataset`.
Learning begins only from that dataset:

```text
episode producer -> EpisodeDataset -> canonical PPO trainer -> model weights
```

The dataset contains only public Vision and information derived from public
Player/Joystick/lifecycle evidence. Trainer and PPO code do not obtain private
Engine state.

Model runtime owns inference, trainable modules, optimizer state, and checkpoint
serialization. `training/work/ppo.py` owns the one PPO update implementation.
`learning/episode_dataset.py` owns the durable episode material and the
five-episode rotation policy.

Training control-plane code owns Train/Evaluate orchestration, result/reward
mapping, update/save timing, and aggregate run metrics. It does not maintain a
second replay buffer or trajectory log.

The model architecture remains replaceable. Planner, Motor Controller, Critic,
and later candidates may change without creating another episode-data path.


## Realtime Mailbox And Update Boundary

Realtime Player/Model transport is intentionally latest-value under inference
backpressure. Newer observations may replace queued intermediate observations;
the system prefers fresh body/world state over a backlog of stale decisions.
The dropped-observation metrics quantify that load shedding. They are not a
request to replay dropped frames later.

This transport rule is independent from policy cadence. Current targets are
120 Hz physics, 60 Hz Motors, and 10 Hz Planner. If effective Motor decisions
arrive much slower than every two world ticks, that is a realtime performance
problem even when mailbox drops are expected.

Current online PPO does not pool multiple policy versions or several episodes.
After one trainable episode is finalized, PPO computes that episode's
reward/GAE, updates the model, publishes the checkpoint, then collection
continues with the updated policy. A future multi-episode rollout design would
need an explicit single-policy-version batch contract and episode-bounded GAE;
it is not the present behavior.


## Realtime Experience Writer

The realtime Model runtime separates inference from durability. Inference
creates immutable DecisionSample/Vision/Proprioception snapshots and enqueues
all episode mutations to one sequential writer. Controller-resolution,
request/result, and accepted-actuation mutations use the same queue, so causal
ordering is preserved without placing SQLite commit latency in the Motor loop.

The terminal boundary is strict: queued experience is drained and committed
before EpisodeDataset is finalized and before PPO reads it. Storage failure is
fail-closed and prevents finalization/update rather than silently dropping
experience.
