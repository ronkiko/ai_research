# Embodied world

`world/` owns stable embodied contracts, the semantic map catalog and
`navigation_v1`. Production GameTable now runs against the versioned
`embodied_world_v1` GameServer mode.

Authority:
- GameServer owns physical position, velocity, effort, zone, epoch/tick and
  on-touch portal transfer;
- GameClient Host owns the transport session and input sequence;
- `navigation_v1` owns semantic action lifecycle, not physics;
- Organism owns learned control and learning artifacts;
- GameTable stores dialogue/social state and observed references only.

The catalog has exactly three map IDs:

~~~text
hallway ⇄ laboratory ⇄ training/flat_run
~~~

`laboratory.workstation` is not a map. `workstation` is a semantic object in
`laboratory`. `migration_workstation` is an initial cutover spawn only; it
does not prove learned arrival or an active seated interaction.

The first physics profile is `flat_1d`, X in [0,1000], with fixed-step 120 Hz
physics. Portal transfer is triggered only by swept physical contact and bumps
the controller fence generation.

## Contracts

`contracts.py` covers EmbodimentBinding, WorldObservation, ActionRequest/
ActionReceipt, SkillBinding, dialogue/tutorial contracts and stable failure
codes. Public navigation exposes semantic IDs only; it has no actuator,
teleport, generic reset, arbitrary entity or set-position tool.

`navigation_v1` provides `describe`, `observe`, `locations`, `navigate`,
`approach`, `interact`, `action_status`, `action_cancel`. Actor binding
is server-side. Cross-zone arrival requires observed membership plus physical
transfer evidence.

Navigation journals jobs and outbound commands before execution. Exact request
IDs are idempotent; uncertain outcomes are reconciled, not blind-replayed.
`organism.lease.BodyLease` prevents navigation and learning TRAIN/VERIFY from
concurrently owning the actuator.

Training setup is a separate privileged internal path. It is limited to
`training/flat_run`, records a receipt, bumps controller generation and always
reports `learned_success=false`.

Check with:

~~~bash
./world/op/check.sh
~~~
