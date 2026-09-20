# Training work

This directory owns the canonical episode material used by Game2 learning.

- `episodes/` is runtime-only and keeps at most the five newest episode SQLite files.
- One episode file contains the public Vision matrices, policy inputs and outputs, terminal result, PPO selection, rewards, advantages, returns, and post-update ratings.
- Realtime and unpaced execution differ only in how quickly they produce rows. Both train through `ppo.py`.
- `--fresh` clears the episode store before training starts.

The SQLite episode file is the training source of truth. Checkpoints remain the persistent model state.
