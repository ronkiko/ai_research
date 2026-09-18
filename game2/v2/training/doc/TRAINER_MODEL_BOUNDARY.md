# Trainer And Model Boundary

Trainer and Player/model communicate through a formal training contract. The
Trainer must not import Player/model implementation modules or Console
internals to obtain privileged gameplay state.

Player/model owns model representation, inference, learning state, and
checkpoint serialization/deserialization. Training owns episode and reward
orchestration, Train versus Evaluate, update/save timing, and aggregate
metrics.
