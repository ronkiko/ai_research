# Motor / Spine audit handoff — 2026-09-25

## Scope and acceptance

The organism is Brain LLM -> Spine CNN (10 Hz) -> learned Motor (60 Hz),
controlling the independently running authoritative world (120 Hz).
First Director task: reach x=987 within ±1 and stop. The current default
laboratory criterion is stricter: ±0.9, exact rest, 0.1 s hold and no wall
contacts. RUN starts from the live state; VERIFY resets its episode.

This patch is a bounded alignment pass, not a new convergence result.

## Validation of this patch

`./gamelab/op/check.sh --existing-server` passed compilation and all 130 unit
tests, including selector resume and both VERIFY entry points. Runtime smoke
then failed before inference: `player1` was already logged in on the running
server. MCP smoke was not reached. No existing player session was logged out.
The full vertical is therefore not green yet. For its next run, arrange free
test players (the smokes use player1/player2) or an isolated backend; do not
evict the Operator's active session to make CI pass.

## Fixed

- Spine resume compares the checkpoint's Motor UUID with the resolved package
  UUID, so `--motor best` can resume when it selects that same Motor. Selection
  of a different Motor remains an error; brain hash checks remain enforced.
- Shell and MCP VERIFY require both `reached` and explicit zero wall contacts.
  Missing wall evidence cannot pass. Tests cover both public paths.
- CLI help points to built UUID instances; README distinguishes the short gate
  from the full learning experiment. Latency comment matches the five-tick bound.

## Current local artifacts

The obsolete `motors/packages/` tree was deleted at the Operator's request.
The active blueprint remains `motors/architectures/continuous_1d/v1/`.
At audit time `motors/instances/` contained no built Motors. The existing ignored
`runtime/spine_motor.pt` still binds the removed `continuous_1d_v1` Motor and
cannot load under the UUID instance contract. It was not migrated or overwritten.
These local artifacts are not part of the Git checkout.

## Next focused brief

Continue from this audit. Inspect current artifacts before acting. Construct and
train a new Motor from the current blueprint, preserve BEST, then certify its
exact frozen brain on all ten held-out programs. Train a fresh Spine against
that concrete UUID in an isolated checkpoint location. Do not relabel old
certificates or transplant a new Motor underneath old Spine weights.

Evaluate the same frozen Spine/Motor pair through the live Host: x=987 from
different positions, small overshoots on both sides, and moving states. Include
nominal, fixed and variable delays. Report final error, velocity, hold ticks,
wall contacts, policy hash and Motor UUID. Separate development results from
held-out results; fixture certificates and passing smoke tests are not learned
competence evidence.

If training fails, inspect the imagined/physical trajectory mismatch first:
near-rest discontinuities, delay-dependent cadence, near-boundary recovery and
finite travel deadlines. The affine predictor deliberately omits wall/rest
discontinuities; physical validation must reject policies exploiting that gap.
Check multiple seeds before claiming reliable convergence. No PID, teacher
actions, hidden correction, changed tolerance or wall-assisted braking.
