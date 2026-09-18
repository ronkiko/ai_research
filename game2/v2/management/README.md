# Management

The operator and "god mode" plane. Management may later select configs, launch
or stop independent Console, Player, and Training processes, and collect
operator telemetry. It is also the future home of the autonomous Research
Strategist, which operates through explicit control-plane capabilities rather
than Player or Training runtime imports.

Management, not Console, decides whether to launch the UI, Console, Player/model,
or Trainer and which experiment/configuration to use. Console receives no
management configuration and has no UI feature toggle.

Management is not a gameplay proxy and does not import runtime objects from
other domains. Current entrypoint: `main.py` placeholder. Local documentation:
`doc/`.

Future Management will choose the Player/model, training algorithm, Train or
Evaluate mode, World, seed, attempt count, Fresh or Resume mode, checkpoint,
and Start or Stop. It will consume Training metrics and results, but will not
compute learning semantics, serialize model state, or become a Trainer/UI
implementation in this architecture patch.

Future Strategist capabilities are mode-scoped:

- **Training:** inspect Training Set structure and Training Maps, train, replay,
  evaluate, compare candidates, and optionally publish StrategyGuidance.
- **Exam:** request an Exam Run and receive PASS/FAIL plus aggregate
  certification metrics. It does not inspect an Exam Map or receive raw exam
  experience.
- **Free Play:** observe aggregate behavior, optionally collect permitted
  learning experience, and optionally request continued training.

These capabilities are not implemented here. Strategist is not a gameplay
hot-path dependency and does not assist gameplay during Exam.
