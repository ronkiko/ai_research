# Game2

Game2 V2 is the sole active implementation.

## Operator entry points

```text
game2/v2/boot.sh
game2/v2/op/screen_server.sh
game2/v2/op/screen.sh
```

`boot.sh` starts the persistent Console and its read-only headless ScreenSource.
`op/screen_server.sh` starts the independent background Screen Server.
`op/screen.sh 1` binds Screen #1 to the currently running Console;
`op/screen.sh 1 off` detaches it without stopping the world.

Vision remains headless and machine-facing. The old graphical Vision/demo
launchers and the all-owning Training launcher remain removed.

Install dependencies with:

```bash
python -m pip install -r game2/v2/requirements.txt
```

See [`v2/README.md`](v2/README.md) for architecture and operation.
