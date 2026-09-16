# Training

The external learning domain. Future Trainer implementations such as
REINFORCE and PPO belong here and communicate with model/player runtimes only
through future formal training contracts.

Training does not own or import Console runtime internals. No runnable Trainer
is implemented in this patch. Local documentation: `doc/`.
