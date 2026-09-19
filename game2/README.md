# Game2

Game2 V2 is the sole active implementation.

## Operator flow

Terminal 1:

```bash
./game2/v2/op/screen_server.sh
```

Terminal 2:

```bash
./game2/v2/op/screen.sh 1
```

Terminal 3:

```bash
./game2/v2/op/train.sh --fresh --screen 1
```

The second command is the graphical foreground Screen. Training only connects
its current Console ScreenSource to that already-running window.

For headless Training:

```bash
./game2/v2/op/train.sh --fresh
```

Vision remains headless and machine-facing. See [`v2/README.md`](v2/README.md)
for architecture and operation.
