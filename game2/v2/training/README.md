# Training

Training is external to Console and communicates with Player/model through a
formal training boundary. The normative target learning, curriculum, dataset,
and exam architecture is defined in
[doc/TRAINING_SYSTEM.md](doc/TRAINING_SYSTEM.md). The first collapsed vertical
is defined in [doc/FIRST_TRAINING_VERTICAL.md](doc/FIRST_TRAINING_VERTICAL.md).

There is no Trainer runtime yet. A Training Episode is a Training-domain
record and does not own or reset the persistent Console `world_tick`.

Training does not import Console runtime internals or Player runtime
implementation modules. Local documentation: `doc/`.
