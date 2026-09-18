# Game2

## Active Implementation

Game2 V2 is the sole active implementation: [`v2/`](v2/).

V1 is **RETIRED**. V2 replaced the V1 architecture and owns the current
authoritative shared-world Console model. V1 remains available in Git history,
but its code is intentionally not retained in the working tree.

Design lessons retained from V1 are documented in
[`v2/doc/V1_LESSONS.md`](v2/doc/V1_LESSONS.md).

## V2 Entry Points

```text
game2/v2/boot.sh
game2/v2/vision.sh
game2/v2/demo.sh
```

Install the current V2 runtime dependency with:

```bash
python -m pip install -r game2/v2/requirements.txt
```

Detailed architecture and operation documentation live in [`v2/README.md`](v2/README.md).
