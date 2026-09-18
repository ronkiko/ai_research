# Dependency Rules

These rules are normative for Game2 V2.

```text
console/*
    MAY import contracts/*
    MUST NOT import player/*, training/*, or management/*

player/*
    MAY import contracts/* and its own modules
    MUST NOT import console/*, training/* internals, or management/*

training/*
    MAY import training-side public contracts
    MUST NOT import console/*, player/*, or management/* runtime modules

management/*
    MAY import contracts/* and launch domains as processes
    MUST NOT import console/*
    MUST NOT import player/*
    MUST NOT import training/*

contracts/*
    MUST NOT import console/*, player/*, training/*, or management/*
```

`contracts` is an architectural leaf. The public Player surface is
`contracts/joystick.py`, `contracts/vision.py`, `contracts/manifests.py`,
`contracts/connection.py`, and `contracts/discovery.py`. Generic framing and
protocol versioning are provided by `contracts/framing.py`. Console
`ActionCommand` and scheduling details remain private under
`console/protocol.py`.

Engine CONTROL, Engine STATE, Engine TELEMETRY, private Engine EVENTS,
`ActionCommand`, `target_world_tick`, and `hold_ticks` are not public Player
contracts.

Player and Training communicate only through the executable formal shared
training contract in `contracts/training.py`. Neither domain imports the other
domain's runtime implementation.

Tests may import all domains because their purpose is to verify these rules.

The architecture test parses the AST rather than grepping source text. It
normalizes `import x`, absolute `from x import y`, and relative
`from ..x import y` forms to absolute module paths before applying the
root-domain rules. Relative imports within the owning domain/subdomain remain
valid, but a relative import that resolves to a forbidden neighboring domain is
treated exactly like its absolute equivalent.
