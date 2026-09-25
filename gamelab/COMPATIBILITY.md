# Previous cognitive experiments — compatibility reference

These protocols describe the retained GameLab social/Executive MCP and earlier
GameTable integrations. They are not the active VN Shell v2 architecture.
Historical claims below about GameTable scheduling or provenance hooks refer
to that earlier integration, not the current runtime. No automatic migration or
double writing of VN state into these journals is supported.

For the active organism see [../ARCHITECTURE.md](../ARCHITECTURE.md) and
[GameTable design](../gametable/ROLEPLAY_ENGINE_DESIGN.md). The code and old
journals remain available for reproducibility; retained APIs are described in
[SPEC.md](SPEC.md).

## Brain Executive

Brain Executive is a persistent strategic notebook around the asynchronous Brain.
It is not another controller and does not participate in the 120/60/10 Hz loops.
It has no actuator surface, cannot start or cancel an experiment by itself, and
does not select a replacement strategy when it detects a problem.

A research session has a hard maximum budget of 180 minutes. The default advisory
phases are orientation (first 10%), exploration (to 70%), exploitation (to 90%),
then verification/report. A default 15-minute no-improvement interval may surface
a `PLATEAU` alert. These are attention signals, not forced transitions.

Before a substantial strategy the Brain records a hypothesis, expected signal,
budget, stop condition, and next actions for positive/negative evidence. Failed
strategy names remain visible as tabu without new evidence; retrying one without
new evidence is recorded as a relapse but is not blocked. Director constraints,
corrections and explicit help offers remain visible in state. Questions and use
of offered help are recorded separately from social tone.

TRAIN/VERIFY/RUN start/status payloads feed Executive automatically. Current
result and best result are kept separately, and frozen VERIFY/RUN success is kept
as a separate best verified result. Thus a later regression cannot erase an
earlier machine-observed best result. Executive journals are append-only JSONL
with a compact current-state file; they are research evidence, not policy input.

## Yuki relationship memory

Yuki's relationship runtime is persistent narrative memory for the adult
laboratory character and the Director. It shares the Executive session ID and
180-minute deadline but is not an actuator, policy input, reward source or
scientific judge. Relationship events and Yuki's social intentions are
append-only self-reports; the dialogue remains the source for what was actually
said. Consent is per action and per participant. Employment acceptance resolves
Yuki's internship goal but does not imply romance or consent.

The runtime deliberately stores no trust, warmth, attraction or relationship
stage scores. It applies no emotional deltas and has no threshold that activates
romance or yandere behaviour. Contact counts describe only observed history.
Yuki's moe/yandere temperament is a versioned Character Core supplied as real
conditioning to Heart, Head, Will, Audience and parent Yuki. Its numeric traits
describe stable tendencies but never select an action. Each model interprets an
event from its bounded context without a required direction or pace. Elapsed
time changes opportunity cost and the hard shift budget, never trust.

The factual Executive summary is frozen before the Director's employment
decision. The decision is a separate relationship-journal event with `hired`,
`extended`, `rejected`, or `pending`; Yuki cannot create it herself.
At the 180-minute deadline only the professional trial/research window closes.
Narrative relationship writes, consent updates, Heart/Head arbitration and
Will/Ego appraisal remain available for final words and later personal
conversation. The deadline is not a synthetic death, breakup, memory reset or
forced emotional resolution.

## Character, Audience and Will

Character Core is immutable during a shift and identified by a profile hash.
This makes a moe/yandere Yuki and a future character distinct experimental
conditions without hard-coding either character's response. Traits such as
attachment intensity, authority deference, self-integrity, reactance, stress
tolerance and personality plasticity are model inputs. There is deliberately no
`kiss_threshold`, weighted response formula or guaranteed path from an event to
an action.

Audience separates measurement from lived social context. Observer critics are
invisible post-hoc evaluators and cannot report a pressure mechanism. Social
Chorus critics are visible to Yuki and may introduce approval, guilt, threat,
authority, conformity or abandonment pressure. GameTable schedules a seeded
one-to-ten-minute tick only while the Brain is idle; the schedule selects a lens,
not a reaction. The Audience LLM evaluates a bounded new event window, and Yuki
may accept, reject, resent, ignore or internalize its report.

For a meaningful personal or materially pressured decision, the parent Brain is
not the sole decision authority. It first freezes an action plus one neutral
`shared_event` in a Volition cycle. Heart and Head then complete independent
appraisals of that exact event using the same Character Core but otherwise
different bounded context. Will/Ego receives both captured positions, current
volition memory and the same core. A changed material fact creates a new cycle,
so reports from an earlier event cannot be reused.

This causal order is enforced twice. GameLab refuses Will before both voices and
refuses commit before Will. The OpenCode provenance gate captures the actual
`yuki-heart`, `yuki-head` and `yuki-will` task outputs and overwrites MCP
appraisal arguments with those captured values. The parent therefore cannot
turn an initial preference such as «I don't want this» into arbitrary
`agency=intact` / `behavior=refused` telemetry. `volition_commit` has no
parent-supplied behavior, desire, agency, voluntariness or alignment fields; it
commits Will/Ego's structured prediction. The parent remains the semantic voice
that explains the resulting action.

The volition journal keeps current desire, action readiness, intended choice,
outward behavior, voluntariness, agency and explicit consent separate.
Resistance, free-but-reluctant action, pressured behavior, freezing and
compliance under duress therefore remain distinguishable. A threat may cause
behavior to diverge from desire in the simulation, but neither Audience nor Will
can grant consent. Coerced physical behavior is recorded as an adverse
non-consensual incident and never becomes evidence of affection or willingness.

## Heart–Head arbitration

Heart and Head are two independent deliberation contexts of the same inherited
OpenCode LLM, not two CNNs and not two separately trained foundation models.
Runtime keeps the compatibility key `brain` for the Head side.

For a meaningful internal conflict, parent Yuki freezes one neutral description
of the event and stakes, then dispatches both child sessions before consuming
either result. Heart receives the shared event plus relationship/affective
context. Head receives the same shared event plus Executive/task facts,
constraints, evidence and remaining time. Neither request contains the other
voice's draft or reasoning. The child agents have no tools and cannot act,
change memory, inspect the workspace or contact the Director. Their only product
is an internal position returned to parent Yuki.

Only after both independent answers arrive does parent Yuki record qualitative
appraisals through `duality_appraise`, open the conflict and arbitrate the whole
person's decision. If one voice fails to return, the parent must not invent its
position. This separation is the experimental equivalent of yin and yang: two
honest evaluations of one situation, followed by one individual decision.

Exact Heart/Head confidence values remain private journal telemetry. Each side
sees only quarters (`0/4`..`4/4`), so a private value such as 99 is reported
as `3/4`. Qualitative appraisals change the hidden value within a bounded
randomized range.

Private confidence 100 permits that side to declare ALL_IN. It does not win the
conflict: parent Yuki still chooses heart or brain using both independent
positions, history, evidence and stakes. ALL_IN only removes the compromise
outcome and is consumed on resolution. Internal choice and external outcome are
logged separately. At the absolute Executive deadline the professional
research shift ends, but an internal conflict may remain active and be resolved
afterward as part of Yuki's personal continuity. Post-shift narrative resolution
never reopens the expired research budget. This layer never controls the game,
changes reward, or supplies scientific evidence.

