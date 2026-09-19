# Management

Management is outside the gameplay data path.

## Screen infrastructure

Screen is deliberately split into two independent pieces:

```text
Screen Server  = background broker only
Screen Window  = foreground operator process
```

Start the broker:

```bash
./game2/v2/op/screen_server.sh
```

Open a persistent Screen window in another terminal:

```bash
./game2/v2/op/screen.sh 1
```

The Screen registers slot 1 with the broker and waits. The broker never launches
Pygame and does not own that graphical process.

Training with `--screen 1` only binds a Console ScreenSource to the registered
slot. UNBIND returns the same window to `Waiting for source...`; it does not
close it.

## Training composition

```bash
./game2/v2/op/train.sh --fresh
./game2/v2/op/train.sh --fresh --screen 1
./game2/v2/op/train.sh --resume
```

Management launches Console, Trainer, Model, and Player as independent OS
processes through their public contracts. Screen is a detachable spectator and
never owns Training.
