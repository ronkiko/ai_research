# Training Domain

Training is outside the Console gameplay domain. The target Trainer is a
separate Python process that coordinates learning through explicit public
contracts, but has no direct reset, tick, physics, world, or internal bus
access.

Training Set Levels contain Training Maps for online learning, unpaced
collection, and offline replay. Each level also has exactly one Exam Map; exam
trajectories are isolated audit data and never become training input.

Online training collects public observations and results from autonomous Console
episodes. Offline replay trains from saved Training Map trajectories without
requiring a Console run for every update. Neither path lets Trainer call
`Engine.step()` or become the Engine clock owner. The complete target semantics
are defined in [TRAINING_SYSTEM.md](TRAINING_SYSTEM.md).
