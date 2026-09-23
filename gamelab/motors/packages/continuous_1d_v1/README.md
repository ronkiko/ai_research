# continuous_1d_v1

Self-contained Motor package for the GameLab 1D body.

Runtime artifacts stay inside this directory:

- `manifest.json` — current training/verification state;
- `brain.pt` — verified Motor weights;
- `candidate.pt` — current Motor School candidate;
- `history.jsonl` — append-only school history;
- `checkpoints/` — archived prior verified brains.

Those runtime files are intentionally ignored by Git. Copying this whole directory
copies the installed motor, its verified brain and its training provenance.
A clean checkout contains only `manifest.default.json`, therefore the motor starts
as **untrained** until Motor School verifies and promotes a candidate.

Socket v1 uses `MotorGoal[4]`. In Motor School, index 0 is normalized desired
velocity and indexes 1..3 are zero/reserved. The deployed Motor also sees only
local normalized velocity and current motor effort. It never receives target_x.
