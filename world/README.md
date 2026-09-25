# Embodied world contracts

This directory is the additive stage-02 boundary for the embodied VN refactor.
It does **not** switch the current GameTable runtime or GameServer scheduler.

Ownership at this stage:

- GameServer remains the authority for physical position, velocity, effort, epoch and tick.
- `world/` defines stable identity/action/observation contracts and the semantic map catalog.
- GameTable remains the active VN until the explicit cutover stage.
- Host/session IDs are transport state and are not character identity or authorization.

The catalog has exactly three map IDs:

~~~text
hallway ⇄ laboratory ⇄ training/flat_run
~~~

`laboratory.workstation` is intentionally not a map. `workstation` is a semantic
object inside `laboratory`.

Each map manifest has independent `physics`, `semantics` and `presentation`
sections. Consumers should use `MapCatalog.physics()`, `semantics()` or
`presentation()` instead of depending on unrelated sections.

The first supported physics profile is `flat_1d`, X in [0,1000]. Hallway
contains the day-start/EXIT anchor at x=0, the first-day Director spawn at x=1,
and an on-touch laboratory portal at x=500. Presentation uses an inclusive
1001-cell X projection; it is not the physical authority.

Typed contracts in `contracts.py` cover:

- EmbodimentBinding and controller generation;
- WorldObservation with source epoch/tick/revision and contract hashes;
- ActionRequest/ActionReceipt and server-side authority references;
- SkillBinding without filesystem paths;
- dialogue message identity/sequence and persisted tutorial flow state;
- request and identity registries that state the idempotency rules for later services.

Contract failures use stable codes such as `unsupported_profile`, `stale_world`,
`identity_mismatch`, `capability_denied`, `skill_missing`, `busy` and
`unknown_outcome`. No contract imports PyTorch, OpenCode or browser code.

Check this boundary with:

~~~bash
./world/op/check.sh
~~~
