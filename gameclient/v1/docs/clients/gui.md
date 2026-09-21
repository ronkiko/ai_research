# GUI Client

The **GUI Client** is the first human graphical interface to GameClient Host.
It is deliberately small: a one-dimensional 0..1000 line, marker `P` for the
shared player, marker `B` for the mob/bomb, and Left/Stop/Right controls.

The GUI polls the Host state, not GameServer. Commands issued by CLI or MCP are
therefore visible in the same GUI session.

Run:

```bash
./gameclient/v1/op/gui.sh
```
