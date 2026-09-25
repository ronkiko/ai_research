# GameTable — embodied decision/action architecture

Статус после этапа 07. GameTable остаётся владельцем персонажа и диалога, но
физический мир больше не редуцируется внутри VN.

## Контуры

~~~text
DirectorIntent
   ↓
Heart + Head
   ↓
CharacterDecision
   ↓
EffectPlanner ───────────────┐
   │ state_effects           │ CharacterActionProposal
   ▼                         ▼
CharacterStateReducer   durable action_outbox
   │                         ↓
provisional social      ActionExecutor
state                        ↓
   │                    world/navigation
   │                         ↓ async
   │                    Spine → Motor → physics
   │                         ↓
   │                    action/world observations
   │                         ↓
   └──────────────→ Narrator + Review
                         ↓
                 character/dialogue publish
~~~

## Contracts

DirectorIntent: `id,text,intent_id`.

CharacterDecision: `disposition,tone`.

EffectPlan:

~~~text
state_effects[]
actions[]  # CharacterActionProposal
duration
~~~

CharacterActionProposal:

~~~text
proposal_id
source = director_request | self_initiated
action_type = navigate | approach
target_id
rationale
observation_ref
scope {capability,target_id}
~~~

The action policy is versioned separately in `roleplay/action_rules.json` so
the stage-07 authority change does not silently invalidate the existing VN save
before migration patch 09.

## Character state reducer

`reduce_character_state` is pure. It may change social stats, fatigue/health
for explicit rest/sleep/conversation effects, narrative minutes, memories and
revision. It rejects unknown effects such as `move` or `lab_work`.

The compatibility symbol `reduce_world` currently aliases this reducer for
older imports, but it no longer owns world state. Existing `scene_id` remains
unchanged and is not evidence of location.

## Physical actions

For accepted Director requests, EffectPlanner creates a semantic proposal:

- request_lab_work from hallway/training → navigate(laboratory);
- request_lab_work while observed in laboratory → approach(workstation);
- request_leave_lab → navigate(hallway).

Decline/clarify produce no physical proposal. A talk turn never becomes a body
command merely because both appraisals preferred accept.

Self initiative has a separate tool-less proposer. Runtime exposes an internal
start hook, but does not call it per frame/tick; a later semantic event/idle
scheduler must provide budget/cooldown.

## ActionExecutor and authority

ActionExecutor validates the complete proposal before dispatch. Public proposal
fields cannot carry entity_id, embodiment_id, x, vx, controller_id or Motor
effort. Actor binding stays server-side in world/navigation.

The executor calls the same canonical NavigationService that backs
`navigation_v1`. OpenCode also supports a dedicated `NAVIGATION_TOOLS`
permission scope for future scoped working contexts; ordinary cognitive voices
remain deny-all. Learning tools are not part of this scope.

Navigation start is asynchronous. `queued/approaching/continuing` are not
arrival. Only observed `arrived` is arrival.

## Persistence and recovery

SQLite now has three independent concerns:

- `turns/save/dialogue`: character/dialogue publication;
- `action_outbox`: approved semantic action and last lifecycle result;
- `world_inbox`: bounded deduplicated observed facts.

Outbox reservation commits before dispatch. A restart after approval but before
an observed dispatch result marks the action uncertain rather than replaying it.
Status polling may update world_inbox even while no dialogue turn is running.
Narrator/review failure therefore cannot roll back the external body.

## Dialogue concurrency

A dialogue turn waits only for the quick action start response, not for route
completion. After the turn publishes, the body worker may continue while the
next conversation is processed. Action status changes produce one notification;
world ticks do not create LLM turns.

Narrator and Review receive the same frozen action results. They may say an
action started only when persisted evidence says so, and may say it arrived only
when status is `arrived`.

## View/browser

If Store has a world observation, server-side affordances use its location.
Otherwise, before cutover, legacy scene is only a fallback hint. Browser never
derives physical location from intent or action text.

Graphics remains the physical presentation boundary. Stage 09 replaces the
non-authoritative legacy frame source with the live embodied source.

## Deferred to later patches

Stage 08 adds learning_v1/workstation learning authority. Stage 09 performs the
full launcher/MCP/data cutover and removes legacy GameLab dependencies. Stage 10
adds Director escort/day-start flow.
