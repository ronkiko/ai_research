# Game2

Game2 V2 is the sole active implementation.

## Operator entry points

```text
game2/v2/boot.sh
game2/v2/op/screen_server.sh
game2/v2/op/screen.sh
game2/v2/op/train.sh
```

Examples:

```bash
./game2/v2/boot.sh
./game2/v2/op/screen_server.sh
./game2/v2/op/screen.sh 1
./game2/v2/op/train.sh --fresh
./game2/v2/op/train.sh --fresh --screen 1
```

Vision is headless and machine-facing. Screen is an independent human observer.
Training is composed from independent processes by Management; there is no
graphical Training owner.

Install dependencies with:

```bash
python -m pip install -r game2/v2/requirements.txt
```

See [`v2/README.md`](v2/README.md) for architecture and operation.
