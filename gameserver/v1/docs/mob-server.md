# Mob Server

Status: normative entity definition for GameServer v1.

## Role

Mob Server produces **intent** for `mob1` / `B`. It never writes position or
velocity directly. Zone Server remains the only authority that applies movement
and updates `x/vx`.

## Current v1 sensing rule

The current mob is intentionally **blind**.

There is no vision sensor contract yet, therefore Mob Server must not read the
player coordinate, nearest-player distance, goal position, or any equivalent
world truth in order to decide movement. It also does not read Telemetry for
decision-making.

Until perception is designed separately, `B` performs an autonomous random
walk:

- once per second by default, Mob Server chooses one intent from `-1, 0, +1`;
- `-1` means left, `0` means stop, `+1` means right;
- Zone Server applies the intent through the same authoritative movement
  contract used for player commands;
- world boundaries are enforced only by Zone Server.

This means `B` may move while no player is logged in. That is intentional:
the world does not wait for a player.

## Future vision behavior

Later, when a physical/legitimate vision sensor contract exists, Mob Server may
change policy from blind random walking to behavior such as:

```text
not seen -> wander
seen     -> pursue according to sensor observation
```

The future policy must depend only on sensor observations explicitly granted to
the mob, never directly on authoritative player coordinates.

## Reproducibility

Normal runtime uses nondeterministic randomness. Laboratory reproduction may
supply an explicit seed:

```bash
python -m gameserver.v1.mob.server --seed 123
```

The seed changes the mob policy sequence only; it does not change Zone
authority or the world clock.
