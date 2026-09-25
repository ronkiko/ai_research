# Characters

Canonical character profiles for Brain agents live here.

Each character owns one directory:

```text
characters/<character-id>/
├── character.json   # machine source of truth used by GameLab and system injection
└── character.md     # human-readable role/context guide for the workstation
```

`character.json` contains stable personality conditioning rather than runtime
relationship state. Runtime memories, current feelings, consent, Executive
state, learned controllers and experiment results belong elsewhere.

The active profile may be overridden with `GAMELAB_CHARACTER_PROFILE`; without
an override Yuki2 uses `characters/yuki-02/character.json`.
