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
- Production graphics source: `EmbodiedWorldGraphics`; LegacyVNGraphics is
  test/history only.
- Public VN launcher: `./gametable/op/start.sh`; public body launcher:
  `./organism/op/organism.sh`.
- Do not reintroduce `game_v1` or `gamelab_v1` into the active character
  config, manuals or CI dependency path.
