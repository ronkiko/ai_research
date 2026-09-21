# GameClient

`gameclient/` is the standalone client line for the MMO-style GameServer laboratory.
The active implementation is [`v1/`](v1/README.md).

It is intentionally a sibling of `gameserver/`, not a submodule of it. Client and
server communicate only through the public Gateway wire protocol.
