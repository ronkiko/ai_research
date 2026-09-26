# Player Gateway

Stage 13 internet-facing presentation/input boundary for ordinary human players.

```text
Browser ⇄ Player Gateway (Node.js) ⇄ GameClient Host[human] ⇄ GameServer

LLM → Spine → Motor → GameClient Host[yuki] ───────────────────────┘
```

Player Gateway is not a GameServer. It never writes coordinates, velocities,
zones, portals, controller generations or physics outcomes. It talks only to the
loopback Director `GameClient Host` on port 17701.

Current Stage 13 responsibilities:

- loopback HTTP + Socket.IO browser endpoint;
- authoritative Host-state → RenderFrame projection at 25 Hz;
- latest-frame / volatile delivery for slow browsers;
- human left/right/release state;
- browser-event rate limiting and coalescing;
- bounded upstream Host input rate plus keepalive;
- one browser control owner for one Host manual lease;
- release on blur/disconnect;
- origin, payload and connection bounds.

GameTable still owns story/dialogue. Stage 14 will proxy/integrate that surface
and make Player Gateway the only production browser entrypoint.

## Local vertical

Start the embodied GameTable stack first so `Host[human]` is present:

```bash
./gametable/op/start.sh --restart
```

Install pinned Node dependencies once, then run the gateway:

```bash
./player-gateway/op/setup.sh
./player-gateway/op/gateway.sh
```

Open `http://127.0.0.1:17881`.

The browser can always observe authoritative frames. Human movement remains
fenced by the Director manual gate; it becomes writable only when the story has
opened the escort/manual-control lease.

Checks:

```bash
./player-gateway/op/check.sh
```
