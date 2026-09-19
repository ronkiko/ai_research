# Game2

## Active Implementation

Game2 V2 is the sole active implementation: [`v2/`](v2/).

V1 is retired. Its historical code remains available in Git history; retained
lessons are documented in [`v2/doc/V1_LESSONS.md`](v2/doc/V1_LESSONS.md).

## V2 Entry Points

```text
game2/v2/boot.sh
game2/v2/op/screen_server.sh
```

`boot.sh` starts the persistent Console. `op/screen_server.sh` manages the
independent background Screen Server.

The previous graphical Vision/demo launchers and unified Training/Exam launcher
were removed in the modular corrective cut. Training composition will be
rebuilt from independent processes in the next patch.

Install the current V2 runtime dependency with:

```bash
python -m pip install -r game2/v2/requirements.txt
```

Detailed architecture and operation documentation live in
[`v2/README.md`](v2/README.md).
