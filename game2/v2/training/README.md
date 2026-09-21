# Training

Game2 V2 has one executable learning path.

Both realtime and unpaced runners produce the same per-episode SQLite dataset
under `training/work/episodes/`. The difference between the modes is only the
rate at which public Vision/action data arrives:

```text
realtime ----\
              -> EpisodeDataset -> training/work/ppo.py -> checkpoint
unpaced -----/
```

The episode dataset is the source of truth for learning and training
inspection. It contains public Vision matrices, policy inputs/outputs, terminal
metadata, rewards, GAE/advantages/returns, PPO selection, and post-update
ratings. At most five episode files are retained; the oldest is rotated out.

`training/work/config.py` owns the shared policy/PPO cadence.
`learning/episode_dataset.py` owns storage and rotation.
`training/work/ppo.py` is the only PPO implementation.

`training/main.py` remains the standalone Trainer control-plane process. It
owns episode orchestration, result/reward mapping, evaluation, and aggregate
metrics. `training/model_runtime.py` is the standalone Model runtime wrapper.

`--fresh` clears the episode dataset store and the four learned checkpoints
(`planner.pt`, `motor.pt`, `critic.pt`, `optimizer.pt`) before starting.
`--resume` requires all four checkpoints. In unpaced mode the same atomic generation also stores curriculum position (current map, next attempt, training seed, episode id, and any pending deterministic Verify), so interruption resumes from the last fully published training state instead of restarting at the first map. Legacy generations without curriculum state are migrated by a frozen greedy pass that finds the first unmastered map.

Checkpoint files are published as one complete generation through an atomic
`.current` pointer; the previous generation is retained. Individual `.pt` paths
remain available, and resume can also read an existing four-file checkpoint.
A failed generation write does not replace the last complete model.

Unpaced collection commits episode rows in bounded batches (up to 32 decisions
or 0.5 seconds between writes), flushing the remaining rows on normal exit or
interruption. An interrupted episode is not used as a completed rollout.
Realtime PPO excludes unconfirmed state-changing commands and observations at
or beyond the terminal tick; no-op policy decisions remain eligible.

Management renders live progress from child events even though child output is
piped. Reaching the per-map attempt limit returns failure while preserving the
latest checkpoint. Set success requires a final frozen pass over all training
maps after curriculum updates.

Training rewards progress toward the goal, adds +1 for success and -1 for
death. A timeout adds no terminal reward; it still fails verification. Controller
requests are measured but carry no cost during skill acquisition. Verification runs after success, every five updates, and at the last permitted
attempt. A map is mastered only after three consecutive successful frozen
(learning-OFF) verification runs; the first failure breaks the streak and
returns control to training. The final frozen set check uses the same 3-in-a-row
criterion for every map.
Evaluation episodes do not consume training random seeds in unpaced mode.

The expensive learning acceptance run is separate from default unit tests:

```bash
python -m game2.v2.tests.training_smoke --seed 1
```

It trains from scratch, requires all-map frozen verification, reloads the saved
checkpoint and checks every training map again. Events and the final result are
kept under `v2/runtime/training-smoke/seed-1/`. Repeat with other seeds to measure
initialization sensitivity. Passing these maps is not evidence of generalization
to an unseen maze or the separate Exam.

Process composition lives in Management:

```bash
./game2/v2/op/train.sh --fresh
./game2/v2/op/train.sh --resume
./game2/v2/op/train.sh --verify
```

`--verify` is read-only qualification of existing checkpoints: no PPO,
checkpoint publication, or curriculum-state mutation. It runs each map in
frozen evaluate mode and requires three consecutive successes. Verification
episodes use a temporary store so normal training episode history is untouched.

To visually inspect the exact frozen policy through Grid Vision:

```bash
./game2/v2/op/screen.sh 1
./game2/v2/op/train.sh --player player1 --verify --screen 1 --view vision
```

To observe realtime training:

```bash
./game2/v2/op/screen.sh 1
./game2/v2/op/train.sh --fresh --screen 1 --view vision --mode realtime
```

The Vision spectator reads the same episode SQLite files for policy ticks and
PPO ratings. There is no separate trajectory JSONL.

Training remains external to Console. A Training Episode never owns or resets
the Console global `world_tick`.
