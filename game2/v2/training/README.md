# Training

Training is external to Console and communicates with the realtime Player/model
through formal contracts.

`training/main.py` is the standalone Trainer. It owns reward mapping,
episode-level learning semantics, mastery evaluation, and aggregate metrics. It
does not launch Console, Player, Model, Management, or graphical code.

`training/model_runtime.py` is the standalone learned Model runtime wrapper.
The Model owns inference, trajectory state, updates, and checkpoints.

Training Set Level 1 is data: `flat_run`, `short_gap`, and `long_gap`.

Process composition lives in Management:

```bash
./game2/v2/op/train.sh --fresh
./game2/v2/op/train.sh --resume
```

`--fresh` resets the known checkpoint pair before starting. `--resume`
requires that pair and continues from it.

Human observation is external to Training. To observe a run, the operator first
starts a persistent foreground Screen in another terminal:

```bash
./game2/v2/op/screen.sh 1
```

and then requests that already-open Screen:

```bash
./game2/v2/op/train.sh --fresh --screen 1
```

Training only sends BIND/UNBIND to Screen Server. It never starts, stops, or owns
the Screen process. The same Screen survives map transitions and returns to
waiting after UNBIND.

A Training Episode remains a Training-domain record and never owns or resets the
Console global `world_tick`.
