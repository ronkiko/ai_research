# Embodied VN acceptance report

## Scope

Acceptance target: the 14-stage embodied VN series defined by
`docs/refactor/embodied-vn/01-concept.md`.

Implementation range audited:

- 02: `72f3a51` — identity/contracts
- 03: `73ec7e8` — physical zones
- 04: `b7e8494` — organism core
- 05: `ab20749` — navigation
- 06: `4ee11e6` — graphics
- 07: `0aba290` — character actions
- 08: `afa59f3` — learning_v1
- 09: `40803bf` — embodied cutover
- 10: `ae8e89c` — first-day/escort
- 11: `b2625a3` — first acceptance audit
- 12: `86e41eb` — realtime Host/World boundary
- 13: `9012645` — Node.js Player Gateway
- 14: this commit — production browser cutover and final deterministic gate

Baseline before stage 11: exact HEAD `ae8e89c20166098c97ce6b7e5e54f81a51f3e57a`
had successful GitHub Actions for Graphics, GameTable, Embodied world, Game v1
and GameLab compatibility checks.

## Result

**Overall: BLOCKED for final series acceptance.**

The deterministic software-contract gate is implemented and is expected to be
green in CI. The plan explicitly forbids treating those tests as evidence of
fresh training convergence, live LLM behavior or human visual escort quality.
Those three evidence classes have not been executed from this GitHub-only audit
environment.

This is not a code blocker for proceeding with experiments; it is an evidence
blocker for writing “A1–A11 fully accepted”.

A follow-up source audit after stage 11 found and closed two code-level gaps:
GameTable now persists and fences the migrated embodiment identity in its own
save, and SkillRegistry revalidates the complete verified SkillBinding before
every production mount. No remaining source-level deviation from A1–A11 is
known after this hardening pass; the remaining BLOCKED items are execution
evidence, not a planned architectural rewrite.

Stages 12–14 then closed the browser realtime architecture discovered during
manual acceptance: Host observation/command lanes are separated, continuous
input no longer drives SQLite fsync cadence, Node.js Player Gateway owns
browser frames/input, and GameTable is now a narrative backend. The deterministic
gate is extended through A13. This still does not manufacture the missing live,
manual or fresh scientific evidence.

## A1–A13 matrix

| Criterion | Automated evidence | Status | Remaining evidence |
| --- | --- | --- | --- |
| A1 one body | `test_acceptance` pins the migrated character/embodiment/entity/Host binding in GameTable SQLite and drives the same `entity.yuki / embodiment.yuki.primary` through hallway → laboratory → training → laboratory → hallway | PASS (contract) | Live stack observation IDs during full scenario |
| A2 explicit control mode | Navigation source has no actuator fallback; scripted escort is separate and absent from training | PASS (contract) | Trace from a verified learned skill RUN |
| A3 confirmed portals | Real `EmbodiedWorldRuntime` swept portal round-trip, receipts, arrival anchors/no immediate bounce | PASS (automated) | Visual confirmation in live browser |
| A4 independent clocks | Real scheduler advances world ticks with no client/LLM/renderer | PASS (automated) | Record measured overruns/latency on operator machine |
| A5 MCP learning | AST/schema gate exposes bounded ID-only Motor/Spine start/status/cancel/verify/select | PASS (contract) | Live LLM calls on isolated learning artifacts |
| A6 semantic access/scope | Active config only navigation_v1/learning_v1; ordinary voices deny-all; actor/coordinate args absent | PASS (contract) | Live scope-denial observations |
| A7 scientific honesty | Assisted setup returns learned_success=false; one-shot/frozen verification is preserved; production mount revalidates embodiment + Motor certificate/hash + Spine + sensor/socket/body/physics contracts | BLOCKED (research) | Fresh Motor+Spine run, held-out VERIFY, artifact IDs/hashes and fresh/trained comparison |
| A8 recovery | Durable outbox crash window becomes uncertain; same proposal is not recreated; existing world/navigation restart tests remain in CI | PASS (contract) | Fault injection against live transport processes |
| A9 graphics | 1001 terrain/occupancy cells, two actors in one cell, stale frame rejected, separate VN dialogue stream | PASS (automated) | Manual two-tab/reconnect visual check |
| A10 cutover | Production config/launcher use embodied world + navigation_v1 + learning_v1 and no active GameLab dependency | PASS (automated) | Clean operator-machine start/stop transcript |
| A11 day/escort | Stage-10 tests + fresh-world reset fence + typing/manual release hardening; P/D placeholders fixed | PASS (contract) | 300-second live timer, browser and GUI escort, sleep/new-day human observation |
| A12 player gateway separation | Browser shell uses Socket.IO Player Gateway for frames/input; GameTable proxy refuses director/frames paths; Node has no direct physics port | PASS (contract) | Live browser capture showing Host[human] path and no direct backend exposure |
| A13 realtime/failure isolation | Host state cache, volatile latest-only frames, input coalescing/rate limits, session/connection bounds, independent gateway process | PASS (contract) | Manual restart/down/flood checks while Yuki/world continue |

## Reproducible commands

Deterministic gate:

~~~bash
./gametable/op/acceptance.sh --automated
~~~

Expected exit code: `0` only if the deterministic A1–A13 contract tests pass.

Live LLM smoke:

~~~bash
./gametable/op/acceptance.sh --live
~~~

This command intentionally exits `3` after a successful live smoke until the
research/manual evidence below exists. Missing OpenCode also exits `3` and is
reported as BLOCKED, not PASS.

Normal component gates used by CI:

~~~bash
./gametable/op/check.sh
./world/op/check.sh
./organism/op/organism.sh check
./op/test-process.sh
python3 -m unittest gametable.tests.test_acceptance -v
python3 -m unittest organism.tests.test_extraction -v
~~~

## Required operator evidence before changing Overall to PASS

### Live LLM / semantic tools

Use a dedicated migrated copy/save and record:

- OpenCode model/version;
- connected MCP list containing only `navigation_v1`, `learning_v1`;
- one accepted navigation request and one refusal/clarification;
- observed action/job IDs and final receipts;
- a new dialogue while the physical job remains active;
- one review rejection/failure that does not rewind external state.

### Fresh research artifacts

Use an isolated `ORGANISM_LEARNING_ROOT` and isolated embodied world save.
Do not consume the operator's existing certificate attempt.

Record before starting:

- curriculum IDs and budgets;
- seeds;
- Motor UUID/generation;
- fixed verification suite IDs and thresholds.

Then record Motor TRAIN → one-shot certification, Spine TRAIN → frozen VERIFY →
`skill_select`, followed by a learned navigation return. Store artifact IDs,
certificate IDs and public hashes, plus fresh/trained held-out results. Assisted
`training_prepare` must be logged separately and excluded from success.

### Manual visual/story run

Record the following from the browser/Director GUI:

- open only the Player Gateway web entrypoint and confirm first-day frame P@0/D@1 and EXIT;
- sidebar Director message followed by reviewed Yuki reply in lower VN window;
- one 300-second active intro (including two-tab and disconnected-UI checks);
- explicit accept causing world_control only after escort start;
- typing focus releases movement; Player Gateway browser lease fencing works;
- Director and Yuki touch the portal independently; no ↑ and no bounce;
- dialogue during escort does not stop the controller;
- sleep from a non-hallway zone creates exactly one next-day Yuki placement,
  does not move Director, and preserves memory/learning artifacts;
- scripted escort recorder, if enabled, remains `scripted_escort_demo` with
  optimizer disabled and does not alter skill/certificate hashes.

## Known limits (not acceptance failures for this series)

The body remains flat_1d. Gravity, 2D/3D humanoid joints, vision, learned sitting,
teacher/imitation following and long-term personality plasticity are outside
A1–A13. Scripted escort is an explicit temporary gameplay controller and is not
evidence of a learned follow skill.
