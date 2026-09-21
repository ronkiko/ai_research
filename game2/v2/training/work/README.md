# Training work

This directory owns the canonical episode material used by Game2 learning.

- `episodes/episode-*.sqlite3` contains only scalar/text trajectory data: policy inputs and outputs, Proprioception, terminal result, PPO selection, rewards, advantages, returns, and post-update ratings.
- `episodes/vision/episode-*.sqlite3` is the matching Vision sidecar. It alone stores zlib-compressed `coarse_physics`, `physics`, and `metadata` matrices.
- The split is deliberate: ordinary SQL inspection of the main episode database can use `SELECT *` without materializing graphical Vision payloads.
- PPO reaches Vision only through `EpisodeDataset`; scalar operations such as progress, counts, trajectory annotations, and spectator ratings stay on the main database.
- Realtime and unpaced execution differ only in how quickly they produce rows. Both train through `ppo.py`.
- `--fresh` clears both the main episode files and their Vision sidecars.
- Rotation keeps matching main/sidecar pairs for the five newest episodes.

The pair of files is the training source of truth for one episode. The main SQLite file is intentionally useful on its own for text/numeric inspection; the Vision sidecar is required only when exact visual replay/training input is needed. Checkpoints remain the persistent model state.
