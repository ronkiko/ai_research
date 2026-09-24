# Spine School: measured dynamics policy search

The default shell and MCP trainer is `measured_dynamics_policy_search_v1`.
The deployment remains the same learned temporal CNN -> frozen learned Motor,
at 10/60 Hz, on the canonical 120 Hz world. No model of physics, planner,
teacher, corrective rule or action threshold participates in VERIFY/RUN.

## Motivation and sources

The former on-policy PPO run at seed 1 completed 200 episodes but stayed at the
first curriculum frontier with 0/10 recent deterministic successes. Its frozen
policy stopped about four units past the target. Increasing verification from
8 to 30 seconds did not correct this equilibrium. Repeating `--fresh` repeated
the experiment, not accumulated learning. The earlier test using a hand-written
test controller established that verification worked, not that training converged.

Three relevant methodological families were considered:

- [TD3, Fujimoto et al.](https://arxiv.org/abs/1802.09477): replay, twin critics
  and delayed policy updates for continuous model-free control.
- [HER, Andrychowicz et al.](https://arxiv.org/abs/1707.01495): learn from failed
  goal-conditioned attempts by relabelling achieved goals. Our exact-rest/hold
  objective needs more care than simply treating every visited position as success.
- [PILCO, Deisenroth and Rasmussen](https://icml.cc/2011/papers/323_icmlpaper.pdf)
  and [Dreamer, Hafner et al.](https://arxiv.org/abs/1912.01603): learn dynamics
  from consequences and optimize policy through predicted trajectories.
- [Tan et al., Sim-to-Real](https://arxiv.org/abs/1804.10332): actuator latency
  and dynamics randomization matter for transferring learned feedback control.
  Our Host traces showed that small delivery delays sustained near-goal
  oscillation which decayed to rest in the ideal-timing unpaced rollout.

The last approach is particularly suitable for this small, fully observed body.
This implementation is a simple deterministic system-identification/policy-search
experiment, **not** PILCO's Gaussian-process uncertainty propagation or Dreamer's
latent-world architecture. Its applicability is deliberately bounded by measured
held-out prediction error and real-world frozen verification.

## Algorithm

1. Collect ordinary `control_loop` episodes through the selected Host. Early
   and periodic episodes explore with Spine's Gaussian policy; Motor is frozen.
2. Record acknowledged 1/60-second intervals: previous velocity, applied effort,
   measured displacement and next velocity. Reject delayed/irregular intervals,
   wall states, and every cancelled/contaminated/invalid episode.
3. Fit an affine predictor with least squares, using normalized velocity/effort
   and an intercept as inputs. All coefficients come from measured transitions;
   there are no copied drag, acceleration or integration equations. Every fifth
   sample is withheld from fitting. Require held-out displacement RMSE <=0.01
   and velocity RMSE <=0.1. Nonzero motion is used for identification; the
   discontinuous physical rest snap is not approximated as successful control.
4. Backpropagate a state cost through predicted motion and the verified frozen
   Motor into the existing Spine CNN. Sample a batch of 48 imagined goals at
   precision/distance scales 1, 5, 20, 80 and 300, plus 800..940-unit transfers,
   in both directions and across the
   world. Training includes precision from the beginning; it cannot become stuck
   behind a frontier gate. One requested episode includes one measured rollout
   and two such batched policy updates. The first 49 updates use 3-second
   imagined horizons, subsequent updates use 8 seconds. After 50 updates, a
   quarter of imagined starting states include velocity toward or away from the
   goal, so braking, overshoot recovery and reversal receive explicit experience.
   After 100 updates the optimizer step decreases from 0.002 to 0.0005.
   At update 200, after the nominal controller has had time to learn the base
   motion task, refine the best physically validated model with a fresh
   optimizer, a 6-second imagined deadline and terminal speed weight 2 instead
   of 0.5. This trains a stopping-time margin; real acceptance still uses the
   unchanged 8-second deadline and exact physical rest. The phase flag and
   optimizer are checkpointed so resuming does not restart refinement.
   Refinement models latency as three environment conditions, all for the same
   Spine: `0` keeps Astra's nominal application timing, `1` keeps Astra's
   fixed one-extra-physics-tick late timing, and `variable` adds a bounded
   server-latency random walk from 1 through 5 extra physics ticks. Each
   variable step differs from the preceding command by at most one tick. At 120 Hz five extra ticks plus normal next-tick application cover about
   50 ms from policy decision to authoritative application. The identified affine
   two-tick predictor is decomposed into stationary one-tick substeps so the
   wider delayed consequences remain derived from measured dynamics rather than
   copied GameServer equations. Imagined refinement is clocked by authoritative
   physics ticks rather than by command count: extra transport delay consumes
   the same finite world-time deadline that it consumes during physical
   validation, and Spine decisions remain tied to their 10 Hz world-time
   cadence. The policy receives only the preceding
   acknowledged application delay as feedback; the next delay is never exposed.
   This is adaptation to measured latency, not prediction of future latency.
5. Every ten updates, evaluate eight fixed development tasks in the canonical
   world, with exploration disabled. Refinement expands this to twelve tasks
   under each of the same three latency conditions `0`, `1` and
   `variable` (36 trials total). The delay apparatus advances the canonical
   world before sending
   an unchanged model action; it does not simulate different physics or steer.
   Preserve the best checkpoint by physical successes, then stopping-time margin,
   remaining position/speed error and wall contacts. Refinement starts a fresh
   best-model comparison under this stronger suite. Continue
   the candidate for the full episode budget even if an earlier candidate passes.
6. Export the best development checkpoint and run the separate six-case frozen
   final VERIFY suite plus six overshoot/reversal cases. A failed VERIFY returns exit code 2; finishing optimizer
   updates alone is never declared successful learning.

The differentiable objective is mean smooth absolute position error along the
trajectory, a speed-squared cost weighted by `1/(1+position_error^2)`, and terminal
position and speed errors. It specifies desired **states**, never desired velocity or motor
effort labels. Unlike a one-time episode-best reward it keeps penalizing departure
from a good state. This versioned school objective is distinct from the persisted
`reward_get/set` instrumentation used for measured episode reports and legacy PPO.
Reward overrides do not silently change this school objective.

The learned predictor is training-only and is not another authoritative simulator:
imagined states never establish success, curriculum competence, wall-contact
evidence or certification. It is not imported by deployed inference.


## Future prediction work

Keep two different prediction problems separate.

**Latency prediction** would try to estimate future server/application delay from
past delay measurements. Exact future latency is generally not knowable from the
current state; useful prediction would mostly concern a moving average or trend,
and possibly recurring high-latency bursts when they have stable temporal
structure. This may be useful later, but it is deliberately not implemented now.
The current Spine only adapts to already measured delay.

**Body-state prediction** is a different and more important future control
problem: estimate where the avatar's body will be after a short horizon from its
current position, velocity, actuator state and learned dynamics. That can let a
controller brake *before* it reaches a target instead of reacting after an
overshoot; the same idea becomes increasingly important for jumps, landings,
balance and multi-joint motion. The present `MeasuredDynamics` predictor is
training-only system-identification infrastructure and is not yet a deployed
forward body predictor. It is a useful foundation, but body prediction should be
designed and verified as a separate future feature.

## Checkpoints and use

```bash
./gamelab/op/train.sh --mode unpaced --motor <motor_uuid> --fresh --episodes 200
# Continue the candidate and retain the best model:
./gamelab/op/train.sh --mode unpaced --motor <motor_uuid> --episodes 100
```

`--fresh` resets Spine and the school, not the mounted verified Motor. Existing
checkpoint bytes are archived by the normal atomic checkpoint writer. The Motor
id and brain hash remain bound to the checkpoint. School state includes candidate
and best weights, optimizer, fitted predictor and observations, update count and
random-generator states. Resuming loads the live candidate, while ordinary
VERIFY/RUN load the exported best model. A legacy PPO checkpoint requires an
explicit fresh start when switching algorithms.

The former PPO method remains an explicit shell experiment via `--algorithm ppo`.
Its frontier curriculum, reward configuration and PPO tests remain available for
comparison. It is not the default trainer and is not a fallback for failed school
verification. MCP and shell use the same school implementation; only world pacing
differs. Short/cancelled service jobs may finish before sufficient identification
or validation data exists; their status must not claim verified competence.

Frozen acceptance is unchanged: error <=0.9, exact `vx=0`, held for 0.1 seconds
of fresh authoritative ticks, and no wall contact. Development tasks, final VERIFY
and additional randomized evaluation must be reported separately. A finite test
suite provides evidence for its tested distribution, not a universal guarantee.

Recovery acceptance explicitly tests small offsets on both sides of a target
and a strategic goal changed to three units behind a moving body. The latter
must show actual velocity reversal followed by exact rest at the target. The
test apparatus changes a goal once; it never supplies a braking/reverse action.

For a server that is already running, the full gate supports
`./gamelab/op/check.sh --existing-server`. It attaches to the existing default
Host and reuses its active `player1` session without logging it out. Tests reset
and control that avatar; a human observer can stay connected but must not send
competing actions. MCP lifecycle smoke additionally creates an owned Host for
`player2` and removes it afterward. No second GameServer is started.

## Research acceptance across seeds

The normal CI gate remains intentionally short. Method-level convergence is
checked only through `./gamelab/op/research.sh --seeds 1,2,3`. Each seed constructs and generation-2-certifies a fresh Motor, trains a
fresh Spine, runs 36-case latency validation, 40 seed-specific held-out goals,
and paced Host/Zone verification. A single seed may be requested for diagnosis
or exact reproduction, but is not treated as robustness evidence.
