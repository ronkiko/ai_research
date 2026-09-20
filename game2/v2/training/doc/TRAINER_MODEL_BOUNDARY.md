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
`training/work/episode_dataset.py` owns the durable episode material and the
five-episode rotation policy.

Training control-plane code owns Train/Evaluate orchestration, result/reward
mapping, update/save timing, and aggregate run metrics. It does not maintain a
second replay buffer or trajectory log.

The model architecture remains replaceable. Planner, Motor Controller, Critic,
and later candidates may change without creating another episode-data path.
