# Tests

## FAST DEFAULT

```bash
python -m unittest discover -s game2/v2/tests -v
```

## SERVER SMOKE

```bash
python -m game2.v2.tests.server_smoke
```

Run this explicit smoke only for persistent Console, attach, or Player
lifecycle changes.

Test-authoring rules: `game2/AGENTS.md`.
