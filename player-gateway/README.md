# Player Gateway

Node.js internet-facing presentation/input boundary for ordinary human players.

```text
Internet Browser
   ↕ HTTPS / Socket.IO
Player Gateway
   ├─ GameTable VN proxy
   ├─ RenderFrame stream
   └─ human input
        ↕
GameClient Host[human] ↔ GameServer

LLM → Spine → Motor → GameClient Host[yuki] ↔ GameServer
```

Player Gateway is not a GameServer. It never writes coordinates, velocities,
zones, portal outcomes or Yuki actuators. Human input reaches only the dedicated
loopback `Host[human]`; authoritative physics remains Python GameServer.

After Stage 14 this is the only production browser entrypoint. GameTable is a
loopback backend for story/dialogue/state and no longer serves realtime frames
or browser Director movement.

## Runtime

Default local entrypoint:

```text
http://127.0.0.1:17881
```

Normal stack startup:

```bash
./gametable/op/start.sh --restart
```

The launcher starts GameServer, `Host[yuki]`, `Host[human]`, Player Gateway
and the GameTable backend. Player Gateway can fail/restart independently without
stopping world ticks or Yuki.

Human realtime path:

- Socket.IO RenderFrame stream at a bounded presentation cadence;
- latest-only volatile frame delivery;
- one browser session owns one Host manual lease;
- normalized `axis_x=-1|0|+1` input only;
- browser event budget + upstream command ceiling + keepalive;
- release on blur/disconnect/shutdown; world watchdog is the last fence.

Narrative path:

- `GET /api/state`, `GET /api/events`, audit reads;
- `POST /api/turn`, `POST /api/story/escort-response`;
- direct `/api/director/*` and old `/api/frames` are not proxied.

## Internet deployment contract

Loopback remains the default. A non-loopback bind requires all of:

- `PLAYER_GATEWAY_PUBLIC=1`;
- explicit allowed origins and hosts;
- `PLAYER_GATEWAY_SESSION_SECRET` with at least 32 characters;
- TLS termination contract via `PLAYER_GATEWAY_TLS_MODE=reverse_proxy` or
  `native_https`.

The gateway issues a signed HttpOnly SameSite player-session capability and
enforces payload, total-connection, per-IP connection and per-session input
bounds. Public errors are sanitized; internal GameServer/Host addresses are not
part of the browser protocol.

Checks:

```bash
./player-gateway/op/check.sh
./gametable/op/acceptance.sh --automated
```
