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
    MUST NOT import console/* or management/* runtime internals

management/*
    MAY know public configs/contracts and launch processes
    MUST NOT import runtime implementation internals from console/player/training

contracts/*
    MUST NOT import console/*, player/*, training/*, or management/*
```

`contracts` is an architectural leaf. The public Player surface is
`contracts/joystick.py` and `contracts/manifests.py`; generic framing is in
`contracts/framing.py`. Console `ActionCommand` and scheduling details remain
private under `console/protocol.py`.

Tests may import all domains because their purpose is to verify these rules.
