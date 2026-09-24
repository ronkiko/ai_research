# continuous_1d / v1

## Related docs

- [GameLab overview](../../../../README.md)
- [Operator runbook](../../../../RUNBOOK.md)
- [Architecture and Motor lifecycle](../../../../ARCHITECTURE.md)
- [Spine School](../../../../SPINE_SCHOOL.md)


This directory is a Motor architecture blueprint, not a trained Motor.

- `architecture.json` is the blueprint contract and carries `revision`.
- `model.py` is the trainable neural architecture.
- Editing this blueprint requires incrementing `revision`.
- Changing the architecture version (for example `v1` -> `v2`) is an
  explicit Operator decision, not an automatic consequence of ordinary edits.

Motor School constructs a new immutable instance under
`gamelab/motors/instances/<motor_uuid>/` by snapshotting this blueprint.
Later blueprint revisions never mutate already-built Motor instances.

Revision 3 binds the generation-2 Motor socket to the GameServer physics
contract fingerprint, including exact-rest semantics. The architecture remains
`continuous_1d/v1`; this is a compatible blueprint revision, not v2.
