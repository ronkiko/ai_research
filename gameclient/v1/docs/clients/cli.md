# CLI Client

The **CLI Client** is the human/automation text interface to GameClient Host.
It is line-oriented and has no TUI, ANSI redraw loop, or hidden screen state.

Examples:

```bash
./gameclient/v1/op/cli.sh players
./gameclient/v1/op/cli.sh login player1
./gameclient/v1/op/cli.sh state
./gameclient/v1/op/cli.sh move right
./gameclient/v1/op/cli.sh events
./gameclient/v1/op/cli.sh move left
./gameclient/v1/op/cli.sh logout
```

Use `--json` for machine-readable output.
