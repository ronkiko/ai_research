# Tests

## FAST DEFAULT

```bash
python -m unittest discover -s game2/v2/tests -v
```

Checks contracts and core in-process semantics.

## SERVER SMOKE

```bash
python -m game2.v2.tests.server_smoke
```

Run only for persistent Console, attach, or Player lifecycle changes.
