# Training

Training is external to Console and communicates with Player/model through a
formal training boundary. The normative target learning, curriculum, dataset,
and exam architecture is defined in
[doc/TRAINING_SYSTEM.md](doc/TRAINING_SYSTEM.md). The first collapsed vertical
is defined in [doc/FIRST_TRAINING_VERTICAL.md](doc/FIRST_TRAINING_VERTICAL.md).

`training/main.py` is a standalone loopback Trainer. It orchestrates one
learned Player connection, owns reward mapping and aggregate metrics, and
never connects to Console or advances the Engine. A Training Episode is a
Training-domain record and does not own or reset the persistent Console
`world_tick`.

Training imports only the executable shared contract and standard-library
runtime code. The Player connects to Console and owns inference, updates, and
checkpoint persistence. Local documentation: `doc/`.
