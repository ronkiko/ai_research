# Training Domain

Training is outside the Console gameplay domain. The target Trainer is a
separate Python process that coordinates learning through explicit public
contracts, but has no direct reset, tick, physics, world, or internal bus
access.

Training owns Training Mode learning and Training Map datasets. Training Set
Levels contain Training Maps for online learning, unpaced collection, and
offline replay. Each level also has exactly one Exam Map; an Exam Run produces
only a certification result and limited aggregate metrics, never a training
trajectory or other learning input.

Online training collects public observations and results from autonomous Console
episodes. Offline replay trains from saved Training Map trajectories without
requiring a Console run for every update. Neither path lets Trainer call
`Engine.step()` or become the Engine clock owner. The complete target semantics
are defined in [TRAINING_SYSTEM.md](TRAINING_SYSTEM.md).

Training may later also coordinate optional Free Play learning when the Research
Strategist explicitly enables it. Free Play is not a Training Set Level, and
Training must never learn from an Exam Map.
