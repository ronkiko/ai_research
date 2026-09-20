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

One episode is one SQLite file in `training/work/episodes/`. The file is the
training source of truth and the universal training log. It stores:

- episode id, mode, source, seed, result, finish tick and progress;
- exact public Vision matrices for each policy decision;
- policy world tick and duration in world ticks;
- motion and current virtual-pad state;
- MotorGoal, action/toggle decision, desired pad state and policy probability;
- SELF/GOAL positions derived from public Vision;
- actuation acknowledgement state;
- reward, GAE, normalized advantage and return;
- PPO-selection flag and post-update value/log-probability/ratio;
- aggregate PPO/update metrics.

The store retains at most the five newest episode files. `--fresh` clears the
store before collection starts.

There is no parallel trajectory JSONL, in-memory replay record type, or separate
realtime/unpaced PPO implementation.

## Policy And Physics Cadence

Console physics stays at 120 Hz. The current learned policy cadence is one
decision per two world ticks. The chosen virtual-pad state is held between
policy decisions.

Discounting uses actual `world_tick` gaps, so the same episode data has the
same learning meaning regardless of wall-clock delivery rate.

## PPO Selection

Credit/reward values are calculated over the full episode sequence. Expensive
PPO optimization then selects:

- a dense terminal tail covering the last 200 world ticks;
- sparse earlier history at approximately one record per 10 world ticks.

This selection policy is shared by realtime and unpaced training and is owned
by `training/work/`, not by either runner.

## Process Ownership

Console owns physics and lifecycle. Player owns public Vision, public Joystick
and actuator timing. Model runtime owns learned inference and weights.
EpisodeDataset owns collected training material. The canonical PPO trainer owns
the gradient update. Training/Management orchestrate episodes and checkpoints.

No training component reads private Engine state or calls an Engine step
through a Trainer-facing API.
