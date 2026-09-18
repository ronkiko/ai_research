# Trainer And Model Boundary

Trainer and Player/model communicate through a formal training contract. The
Trainer must not import Player/model implementation modules or Console
internals to obtain privileged gameplay state.

Trainer targets a trainable component or model candidate, not necessarily an
entire Player. Its target may be a Motor Controller, a Planner, an MLP, a CNN,
an SNN, or another trainable implementation. Trainer must not need to know
which implementation it is training.

The model/component owns its representation, inference, learning state, and
checkpoint serialization/deserialization. Training owns episode and reward
orchestration, Train versus Evaluate, update/save timing, and aggregate
metrics. Research Strategist may decide what should be trained or evaluated,
but it does not perform model-specific learning itself.

The first `3-8-2` MLP is a collapsed direct-action baseline. It does not
redefine the future Motor Controller boundary: the target Motor Controller
will consume `MotorGoal` and fast allowed sensory/motion information, while a
Planner handles higher-level gameplay reasoning.
