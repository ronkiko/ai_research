# Training

Training is external to Console and communicates with the realtime Player/model
through formal contracts.

`training/main.py` is the standalone Trainer. It owns reward mapping,
episode-level learning semantics, mastery evaluation, and aggregate metrics. It
does not launch Console, Player, Model, Management, or graphical code.

`training/model_runtime.py` is the standalone learned Model runtime wrapper.
The Model owns inference, trajectory state, updates, and checkpoints.

Training Set Level 1 is data: `flat_run`, `short_gap`, and `long_gap`.

Process composition does not live in the Training domain. Management launches
independent Console, Trainer, Model, and Player processes through their command
and wire contracts:

```bash
./game2/v2/op/train.sh --fresh
./game2/v2/op/train.sh --resume
```

Human observation is outside Training. An optional Management-only
`--screen N` binds the current Console ScreenSource to Screen Server but does
not alter Trainer, Model, Player, rewards, observations, or action timing.

A Training Episode remains a Training-domain record and never owns or resets the
Console global `world_tick`.
