# GameTable — VN Shell v2 agent contract

The active experiment is yuki-vn-1, an adult fictional researcher. The canonical
profile, intents, scene graph and world rules live in roleplay/rules.json.
Archived Character Core / relationship / duality / Volition protocols are not
part of this runtime.

The human is the Director. Browser requests are DirectorIntent objects with
id, text and intent_id. Never treat an intent as an already completed world
action.

## Cognitive roles

APPRAISAL:
- Heart and Head run in separate fresh child sessions.
- They receive the same frozen packet and never see each other's report.
- They score disposition only: respond, accept, decline, clarify.
- They may estimate social impact but cannot change GameState.

NARRATION:
- receives fixed before-state, CharacterDecision, EffectPlan, applied world
  effects, after-state and observed external results;
- verbalizes those facts only;
- may not invent movement, work, tool success, Director actions or new state.

REVIEW:
- checks a draft against the same frozen facts;
- cannot replace decision, tone, effects or state.

LABORATORY:
- exists only behind ExternalExecutor;
- uses the session permission allowlist supplied by GameTable;
- reports observed tool results, not invented success;
- an async *_start is not completion.

## Ownership

DecisionEngine chooses CharacterDecision.
EffectPlanner creates typed effects.
WorldReducer is the only layer that changes scene_id, game time or stats.
ExternalExecutor is the only roleplay layer allowed to open an MCP-enabled
OpenCode context.
Store/SQLite is the only authoritative persisted state.
ViewProjector creates browser-facing ViewState and affordances.
Browser renders and submits intents; it does not implement world rules.

Health, fatigue, mood, affection and trust are game values, not medical
measurements. Affection, trust, agreement and consent remain distinct concepts.

## Development invariants

Keep reducers pure and replayable. Do not introduce client-side scene-transition
rules, direct location mutation, legacy activity requests, arbitrary set_stats
endpoints or MCP access in ordinary voices.

Persist an approved external effect before executing it. On ambiguous transport
failure mark it uncertain and do not blind-retry it.

Do not publish unreviewed drafts. One event_id must never mutate state twice.
Live tests must use temporary saves, delete owned OpenCode sessions and avoid
training, run, player movement or other destructive laboratory actions.

Run:

~~~bash
./gametable/op/check.sh
~~~

Optional real-model/MCP smoke:

~~~bash
./gametable/op/check.sh --live
~~~

Read README.md and ROLEPLAY_ENGINE_DESIGN.md before changing the runtime boundary.
