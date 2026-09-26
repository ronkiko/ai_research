# GameTable agent contract — embodied actions

DirectorIntent является запросом человека, а не фактом мира.

Heart и Head:
- fresh independent child sessions;
- один frozen packet;
- без tools;
- оценивают disposition/social impact, но не выполняют действия.

CharacterAction proposer:
- отдельный fresh tool-less Brain context;
- возвращает proposal или null;
- proposal — не side effect и не receipt;
- self initiative вызывается только bounded event/idle scheduler, не tick loop.

Narrator и Review:
- без tools;
- получают frozen decision, state effects, approved proposals и persisted action results;
- queued/approaching нельзя описывать как arrived;
- world observation/receipt имеет больший авторитет, чем текст или legacy scene.

## Ownership

DecisionEngine выбирает CharacterDecision.
EffectPlanner формирует state effects + CharacterActionProposal.
CharacterStateReducer меняет только GameTable social/resource/narrative state.
Он никогда не меняет physical location.
ActionExecutor — server-side semantic action boundary.
world/navigation владеет navigation lifecycle.
GameServer владеет x/vx/effort/zone/ticks.
Store хранит dialogue state, durable action references и bounded observed-fact inbox.
Graphics рендерит world snapshots.
Browser не реализует world rules.

`scene_id` в migrated save — только historical compatibility field.
Production код не использует его как доказательство физической локации.

## Side-effect invariants

1. Durable action_outbox записывается до dispatch.
2. request_id/proposal_id идемпотентны.
3. Unknown dispatch outcome = uncertain; blind replay запрещён.
4. LLM не задаёт entity/embodiment/x/vx/motor_x.
5. ActionExecutor проверяет semantic target/scope кодом.
6. Arrival утверждается только по navigation/world observation.
7. Narrator failure не отменяет сохранённый external fact.
8. Ordinary voices deny-all; navigation/learning permission scopes не смешиваются.

Не возвращать прямой VN `move`, `scene_id = ...`, client-side transitions или
старый laboratory_step как способ физического перемещения.

Run: `./gametable/op/check.sh`.

## Cutover 09

- Active OpenCode MCPs: only `navigation_v1` and `learning_v1`.
- Production browser: Player Gateway on port 17881, Socket.IO RenderFrame
  from authoritative Host observation. Python graphics is the parity oracle;
  legacy `/api/frames` is not a production backend route.
- Public VN launcher: `./gametable/op/start.sh`; public body launcher:
  `./organism/op/organism.sh`.
- Do not reintroduce `game_v1` or `gamelab_v1` into the active character
  config, manuals or CI dependency path.

## First-day story / Director escort

- `story_flow` — durable explicit story state, separate from social stats.
- 300-second intro uses connected VN presence, not world ticks or reducer minutes.
- Escort consent must reference the published offer_id; silence/arrows/stats are
  never consent.
- Director is a separate actor/session/controller and may be driven only through
  the fenced Director Host after manual gate opens.
- `scripted_escort` is an explicit non-learned temporary controller. Never
  report it as a Spine skill, TRAIN/VERIFY success, or certificate.
- Escort, navigation and learning share Yuki's BodyLease.
- Sleep/day-start placement is an idempotent story montage action with
  learned_success=false; never move Director as a side effect.
- Restart must release effort and reconcile; never auto-resume an escort from a
  stale leader/manual-control observation.

## Approved work contexts

GameTable MCP exposes `execute_approved(approval_id)` plus read-only discovery.
The server binds action, target, identity and learning parameters; models do not
choose artifacts, budgets or setup authorization. Ordinary voices stay deny-all.
Workstation interaction and physical training are separate semantic actions.
Current Motor/Spine practice budgets are 100 episodes each, not a promise of
certification. Manual/scientific acceptance remains pending; see
`../docs/reports/series-2-gameplay-acceptance.md`.
