# Training

Training is external to Console and communicates with Player/model through
formal contracts. The normative target learning, curriculum, dataset, and exam
architecture is defined in
[doc/TRAINING_SYSTEM.md](doc/TRAINING_SYSTEM.md). The first vertical contract is
defined in [doc/FIRST_TRAINING_VERTICAL.md](doc/FIRST_TRAINING_VERTICAL.md).

`training/main.py` is the standalone Trainer process. It owns reward mapping,
episode-level learning semantics, and aggregate metrics. It never imports or
starts Console, Player, Management, or graphical code.

`training/model_runtime.py` is only the executable wrapper for the separate
learned Model runtime. The Model runtime owns model representation, inference,
trajectory state, updates, and checkpoints.

The temporary unified Training/Exam process launcher has been removed. Training
currently has no top-level composition command by design. The next patch will
reassemble Console, Player, Model, and Trainer as independent processes through
explicit contracts instead of restoring an all-owning launcher.

Training Set Level 1 remains available as data with `flat_run`, `short_gap`, and
`long_gap` maps. Its Exam resource is currently only the opaque identity
`platformer-level-1-exam`; no Exam Map bytes are stored here.

A Training Episode is a Training-domain record and does not own or reset the
Console `world_tick`. Human Screen observation is outside Training and is never
required for learning to run.
