# Training

Training is external to Console and communicates with Player/model through a
formal training boundary. The normative target learning, curriculum, dataset,
and exam architecture is defined in
[doc/TRAINING_SYSTEM.md](doc/TRAINING_SYSTEM.md). The first collapsed vertical
is defined in [doc/FIRST_TRAINING_VERTICAL.md](doc/FIRST_TRAINING_VERTICAL.md).

`training/main.py` is a standalone loopback Trainer. It orchestrates one
Realtime Player connection, owns reward mapping and aggregate metrics, and
never connects to Console or advances the Engine. The separate
`training/model_runtime.py` process owns model representation, stochastic
inference, trajectory records, updates, and checkpoints. A Training Episode is
a Training-domain record and does not own or reset the persistent Console
`world_tick`.

Training Set Level 1 is now available as the first resource with `flat_run`,
`short_gap`, and `long_gap` maps. Its Exam resource is currently only the
opaque identity `platformer-level-1-exam`; no Exam Map bytes are stored here.

Training imports only executable shared contracts and standard-library
orchestration code. Realtime Player connects to Console and owns lifecycle,
public peripherals, and physical action timing. Local documentation: `doc/`.
