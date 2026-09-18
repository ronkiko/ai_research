# V1 Lessons

V1 is retired. This document preserves design lessons only; it does not
preserve the V1 implementation or define a future protocol. The current
architecture is defined by the Game2 V2 documentation. If a V1 lesson
conflicts with V2 architecture, the V2 architecture has priority. The
historical V1 implementation is available only through Git history.

## Small Trainable Player

A small, transparent Player model is useful for laboratory experiments. A
possible baseline can use conceptual inputs such as:

- distance to relevant terrain or a gap;
- grounded or contact information;
- motion or speed information.

The corresponding conceptual outputs are independent `Right` and `Jump`
decisions. V1 used a small 3-8-2 MLP, but that shape is one possible baseline,
not a permanent V2 architecture.

Player/model code belongs outside Console. Console must not import model or
training implementations.

## Independent Actions

`Right` and `Jump` are independent decisions. A model decision should not be
masked merely because the current physics cannot apply it. For example, a
model may choose Jump while airborne; Physics decides whether that choice has a
physical effect. Keeping the choice visible supports honest analysis of policy
behavior. The V1 protocol implementation is not carried forward.

## Candidate Training Baseline

Candidate ideas for future V2 Training include:

- episodic policy-gradient or REINFORCE as a simple baseline;
- positive terminal reward for success and negative terminal reward for failure;
- entropy encouragement;
- a reward baseline;
- gradient clipping;
- sampling actions in training mode;
- deterministic or greedy actions in evaluation mode;
- no weight updates during evaluation.

A Training Episode belongs to the V2 Training domain. It is not a World, an
Engine lifecycle, a world clock, or a Console reset unit. V2 Console owns one
persistent `world_tick`; Training may record `episode_id`,
`start_world_tick`, `finish_world_tick`, and `result`, but it does not create a
separate physical clock.

## Training Integrity

A training update should account only for decisions that actually reached the
intended simulation time. An action still scheduled in the future after a
terminal result must not receive credit for that result.

Transport and scheduling quality should remain diagnostically visible, for
example as `accepted`, `late`, and `rejected`. These conditions must not be
silently presented as a clean experiment. This is a future Training-side
requirement to adapt to V2 contracts, not a request to carry forward the old
V1 acknowledgement or protocol implementation.

## Checkpoints

Useful checkpoint concerns for future V2 Training are:

- an explicit checkpoint version;
- model architecture identity;
- model weights;
- optimizer state for training resume;
- training counters and other continuation state;
- RNG state for reproducible continuation;
- atomic checkpoint writes;
- explicit Resume and Fresh modes;
- optionally saving a backup of the previous checkpoint before Fresh.

Future V2 Training must define its own format. No V1 checkpoint
implementation is normative.

## Metrics

Useful operator and training metrics include:

- attempts and successes;
- cumulative and rolling success rates;
- a rolling result window;
- terminal duration or ticks;
- loss;
- the number of actual training updates;
- late and rejected actions;
- requested versus physically applied actions where useful.

Loss is not gameplay quality. The primary experiment-quality metric should
reflect behavioral results, not only optimizer loss.

## Sensory Feature Extraction

Semantic visual observation can be transformed on the Player side into compact
model inputs. For example, Vision may support extracting distance to a terrain
discontinuity and a support or grounded-like visual condition.

V1 also supplied horizontal velocity as separate metadata. V2 must not expose
privileged Engine state to a model without a formal public contract. Future
options include inferring motion from Vision history or defining a formal
public proprioception peripheral if that capability is accepted architecturally.
No choice between those options is made here.

## Generalization Across Worlds

Training and evaluation are useful on different World geometries. A future V2
experiment should be able to train or tune a Player/model on one World and
evaluate that same Player/model on another geometry.

Any future evaluation map must use the current V2 World format and semantics.
The former V1 short-pit map is not carried into V2 by this retirement patch.

## Management and Cockpit Ideas

The following are useful future V2 Management capabilities, recorded as
product ideas rather than an implementation requirement:

- Play, Train, and Evaluate;
- choose World/map, Player/model, and algorithm;
- choose episode or attempt count and seed;
- choose checkpoint and Resume or Fresh;
- run realtime experiments and, later, accelerated experiments;
- Start and Stop;
- show rolling metrics, history, and diagnostics;
- export results separately from runtime state.

Management may launch independent processes and consume operator telemetry. It
must not import Console, Player, or Training runtime implementations.

## Safe Operator UX

Useful behavioral requirements are:

- an invalid draft configuration must not destroy a running experiment;
- operator or UI errors must be visible without crashing unrelated runtime;
- a missing or corrupt checkpoint must be an actionable operator error;
- operator UI work must not block the realtime World;
- slow UI work must not block simulation;
- live metrics may use latest-value semantics;
- completed results and history must not be silently lost;
- experiment results can be exported separately from runtime state.

These are behavior ideas, not V1 code to retain.

## Intentionally Not Carried Forward

The following were retired implementation assumptions, not future V2 backlog:

- one GameContainer equals one Player;
- global episode lifecycle inside Engine or game;
- episode tick as the physical clock;
- global reset;
- reset starts a new World attempt by resetting the game clock;
- single-controller server model;
- V1 binary protocol;
- V1 SessionController importing game, model, or trainer objects;
- privileged training metadata without a formal public peripheral;
- cockpit owning runtime objects;
- V1 socket topology.
