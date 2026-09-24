"""Measured system identification and differentiable Spine policy search.

The predictor is fitted to acknowledged Motor intervals, never populated with
GameServer equations. It is training infrastructure only. All physical evidence
and certification come from control_loop in the canonical world. This is a small
fully observed model-based policy-search experiment, not an implementation of
PILCO or Dreamer. There are no teacher actions or runtime planning/controller.
"""
from __future__ import annotations

import copy
import json
import random
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

import torch

from .config import (
    HISTORY_FRAMES,
    MOTOR_HZ,
    PHYSICS_HZ,
    PLAYER_MAX_SPEED,
    SPINE_HZ,
    SPINE_GOAL_DISTANCE_SCALE,
)
from .models import build_spine_policy, load_checkpoint, motor_checkpoint_extra, policy_id, save_checkpoint
from .runtime import checkpoint_path, ensure_player
from .reward import RewardConfig, RewardStore
from .training import _prepare_reward_config, collect_episode, verify_spine_policy, verify_recovery_policy

ALGORITHM = "measured_dynamics_policy_search_v1"
BATCH_SIZE = 48
SCALES = (1., 5., 20., 80., 300., 900.)
# Development tasks for checkpoint selection. Final VERIFY uses a distinct suite.
VALIDATION_CASES = ((150., 163.), (850., 837.), (350., 385.), (650., 615.),
                    (130., 710.), (870., 290.), (30., 970.), (970., 30.))
REFINEMENT_CASES = (*VALIDATION_CASES, (900., 650.), (100., 350.),
                    (100., 890.), (900., 110.))
# Latency is an environment condition, not a separate policy capability.
# Refinement keeps Astra's fixed 0/1 extra-tick cases and adds one variable
# server-latency case. At 120 Hz, six extra ticks add 50 ms of waiting and
# produce about 58.3 ms policy-decision->authoritative-application latency.
MAX_VARIABLE_DELAY_TICKS = 6
DELAY_MODES = ("0", "1", "variable")


def delay_mode_schedule(
    horizon: int,
    batch_size: int,
    *,
    generator: torch.Generator,
) -> torch.Tensor:
    """Balanced 0 / 1 / variable delay conditions for imagined refinement.

    Variable latency is a bounded random walk. Its next value differs by at
    most one physics tick from the previous command; future delay is never
    exposed to the policy.
    """
    if horizon <= 0 or batch_size <= 0:
        raise ValueError("delay schedule dimensions must be positive")
    schedule = torch.zeros((horizon, batch_size), dtype=torch.long)
    lane = torch.arange(batch_size) % 3
    schedule[:, lane == 1] = 1
    variable = lane == 2
    current = torch.randint(
        1,
        MAX_VARIABLE_DELAY_TICKS + 1,
        (batch_size,),
        generator=generator,
    )
    for step in range(horizon):
        if step:
            current = (
                current
                + torch.randint(-1, 2, (batch_size,), generator=generator)
            ).clamp(1, MAX_VARIABLE_DELAY_TICKS)
        schedule[step, variable] = current[variable]
    return schedule


class DelayedCommands:
    """Transport-only latency stress; actions and authoritative physics are unchanged."""

    def __init__(self, client, *, mode: str):
        if mode not in DELAY_MODES:
            raise ValueError(f"delay mode must be one of {DELAY_MODES}")
        self.client = client
        self.physics_hz = client.state()["snapshot"]["physics_hz"]
        self.mode = mode
        self.rng = random.Random(709)
        self.variable_ticks = self.rng.randint(1, MAX_VARIABLE_DELAY_TICKS)

    def __getattr__(self, name):
        return getattr(self.client, name)

    def _extra_ticks(self) -> int:
        if self.mode == "0":
            return 0
        if self.mode == "1":
            return 1
        self.variable_ticks = max(
            1,
            min(
                MAX_VARIABLE_DELAY_TICKS,
                self.variable_ticks + self.rng.choice((-1, 0, 1)),
            ),
        )
        return self.variable_ticks

    def motor(self, effort):
        advance = getattr(self.client, "advance_tick", None)
        for _ in range(self._extra_ticks()):
            if callable(advance):
                advance()
            else:
                time.sleep(1 / self.physics_hz)
        return self.client.motor(effort)


class MeasuredDynamics:
    """Affine local predictor [vx, effort, 1] -> [delta_x, next_vx].

    Normalization is sensor calibration, not a dynamics formula. Rest snap and
    wall collisions are discontinuities: fit free-motion samples and validate
    exact rest exclusively in the real environment. Reject an inaccurate fit.
    """

    def __init__(self) -> None:
        self.samples = deque(maxlen=8192)
        self.weights: torch.Tensor | None = None
        self.metrics: dict = {}

    def add(self, samples) -> None:
        self.samples.extend(row for row in samples if abs(row[3]) > 0.1)

    def fit(self) -> bool:
        if len(self.samples) < 48:
            return False
        data = torch.tensor(list(self.samples), dtype=torch.float64)
        x = torch.stack((data[:, 0] / PLAYER_MAX_SPEED, data[:, 1],
                         torch.ones(len(data), dtype=data.dtype)), dim=-1)
        y = data[:, 2:] / PLAYER_MAX_SPEED
        test = torch.arange(len(data)) % 5 == 0
        if torch.linalg.matrix_rank(x[~test]) < 3:
            return False
        weights = torch.linalg.lstsq(x[~test], y[~test]).solution
        rmse = ((x[test] @ weights - y[test]).square().mean(0).sqrt() * PLAYER_MAX_SPEED)
        self.metrics = {"samples": len(data), "heldout_dx_rmse": float(rmse[0]),
                        "heldout_vx_rmse": float(rmse[1])}
        if not torch.isfinite(weights).all() or rmse[0] > .01 or rmse[1] > .1:
            raise RuntimeError(f"local dynamics failed held-out validation: {self.metrics}")
        self.weights = weights.float()
        return True

    def state_dict(self) -> dict:
        return {"samples": list(self.samples), "weights": self.weights, "metrics": self.metrics}

    def physics_tick_weights(self) -> torch.Tensor:
        """Recover one stationary physics-tick affine step from measured 2-tick data.

        The coefficients are derived from acknowledged Motor intervals rather
        than copied from GameServer equations. Rows are [vx_norm, effort, 1];
        columns are [dx_norm, next_vx_norm].
        """
        if self.weights is None or self.weights[0, 1] <= 0:
            raise RuntimeError("identified dynamics do not admit a positive substep")
        a = self.weights[0, 1].sqrt()
        scale = 1 + a
        b = self.weights[1, 1] / scale
        e = self.weights[2, 1] / scale
        c = self.weights[0, 0] / scale
        d = (self.weights[1, 0] - c * b) / 2
        f = (self.weights[2, 0] - c * e) / 2
        tick = torch.stack((
            torch.stack((c, a)),
            torch.stack((d, b)),
            torch.stack((f, e)),
        ))
        if not torch.isfinite(tick).all():
            raise RuntimeError("identified dynamics produced nonfinite physics-tick model")
        return tick

    def half_interval_delay_effect(self) -> torch.Tensor:
        """Compatibility view of the existing one-extra-tick delay effect."""
        tick = self.physics_tick_weights()
        a, b = tick[0, 1], tick[1, 1]
        c, d = tick[0, 0], tick[1, 0]
        return torch.stack((c * b + d, a * b))

    def predict_delayed_interval(
        self,
        velocity: torch.Tensor,
        previous_effort: torch.Tensor,
        new_effort: torch.Tensor,
        extra_ticks: torch.Tensor,
        *,
        max_ticks: torch.Tensor | None = None,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """Roll measured local dynamics through 0/1/variable transport delay.

        max_ticks truncates the interval at a physical-time deadline. This is
        essential for variable latency: a delayed command may consume several
        authoritative ticks, but imagined training must still end at the same
        world-time horizon as physical validation.
        """
        velocity, previous_effort, new_effort = torch.broadcast_tensors(
            velocity, previous_effort, new_effort
        )
        extra_ticks = torch.as_tensor(
            extra_ticks, dtype=torch.long, device=velocity.device
        )
        extra_ticks = torch.broadcast_to(extra_ticks, velocity.shape)
        if bool((extra_ticks < 0).any()):
            raise ValueError("extra delay ticks must be nonnegative")

        # Nominal Motor observation spans two physics ticks. A delayed command
        # holds the prior effort for d ticks, then the new effort is applied for
        # one authoritative tick before the next observation.
        interval_ticks = torch.where(
            extra_ticks > 0,
            extra_ticks + 1,
            torch.full_like(extra_ticks, PHYSICS_HZ // MOTOR_HZ),
        )
        if max_ticks is not None:
            max_ticks = torch.as_tensor(
                max_ticks, dtype=torch.long, device=velocity.device
            )
            max_ticks = torch.broadcast_to(max_ticks, velocity.shape).clamp_min(0)
            interval_ticks = torch.minimum(interval_ticks, max_ticks)
        tick_weights = self.physics_tick_weights().to(
            dtype=velocity.dtype, device=velocity.device
        )
        dx = torch.zeros_like(velocity)
        vx = velocity
        for substep in range(int(interval_ticks.max().item())):
            active = substep < interval_ticks
            use_new = (extra_ticks == 0) | (substep >= extra_ticks)
            effort = torch.where(use_new, new_effort, previous_effort)
            predicted = torch.stack(
                (vx, effort, torch.ones_like(vx)), dim=-1
            ) @ tick_weights
            dx = dx + torch.where(active, predicted[..., 0], 0.0)
            vx = torch.where(active, predicted[..., 1], vx)
        return dx, vx, interval_ticks

    def restore(self, state: dict) -> None:
        self.samples.extend(state.get("samples", []))
        self.weights = state.get("weights")
        self.metrics = state.get("metrics", {})


class SpineSchool:
    def __init__(self, model, *, seed: int) -> None:
        self.model = model
        self.optimizer = torch.optim.Adam(model.spine.parameters(), lr=.002)
        self.dynamics = MeasuredDynamics()
        self.generator = torch.Generator().manual_seed(seed)
        self.exploration_state = torch.Generator().manual_seed(seed).get_state()
        self.rng = random.Random(seed)
        self.updates = 0
        self.best = None
        self.best_score = None
        self.best_validation = None
        self.rest_refinement = False

    def update(self, *, cancel: threading.Event | None = None) -> dict:
        if self.dynamics.weights is None:
            return {"updated": False, "reason": "collecting dynamics evidence"}
        model = self.model
        if self.updates >= 300 and not self.rest_refinement:
            # Refine the best measured policy with a stricter imagined deadline
            # and terminal rest cost. Real acceptance remains unchanged at 8 s.
            if self.best is not None:
                model.load_state_dict(self.best)
            self.optimizer = torch.optim.Adam(model.spine.parameters(), lr=.0005)
            self.rest_refinement = True
            # Refinement uses a stricter, transport-stressed selection suite.
            self.best = self.best_score = self.best_validation = None
        scale = torch.tensor(SCALES).repeat_interleave(BATCH_SIZE // len(SCALES))
        dx = (torch.rand(BATCH_SIZE, generator=self.generator) * 2 - 1) * scale
        # Give the finite travel deadline actual coverage: uniform 0..900
        # sampling otherwise supplies very few almost-world-width transfers.
        far = scale == max(SCALES)
        dx = torch.where(far, torch.sign(dx) * (800 + dx.abs() * (140 / 900)), dx)
        x = torch.rand(BATCH_SIZE, generator=self.generator) * (960 - dx.abs()) + 20 + torch.relu(-dx)
        target = x + dx
        velocity = torch.zeros(BATCH_SIZE)
        if self.updates >= 50:
            # State distribution includes momentum both toward and away from
            # the goal. The policy must learn braking and reversal itself.
            moving = torch.arange(BATCH_SIZE) % 4 == 0
            velocity = torch.where(moving, .6 * (
                2 * torch.rand(BATCH_SIZE, generator=self.generator) - 1
            ), velocity)
        effort = torch.zeros(BATCH_SIZE)
        input_delay = torch.zeros(BATCH_SIZE)
        def frame():
            return torch.stack((x / 500 - 1, velocity, effort,
                                torch.tanh((target - x) / SPINE_GOAL_DISTANCE_SCALE)), -1)

        history = frame().unsqueeze(-1).repeat(1, 1, HISTORY_FRAMES)
        # Short imagined horizons stabilize early gradients; real evaluation
        # always uses the full physical task horizon, not imagined success.
        horizon = 180 if self.updates < 49 else (420 if self.rest_refinement else 480)
        # Astra's fixed 0/1 latency cases remain intact as explicit lanes.
        # Variable server latency is the third environment condition. Spine gets
        # only the preceding acknowledged delay, never the next sampled value.
        delay_schedule = (
            delay_mode_schedule(
                horizon,
                BATCH_SIZE,
                generator=self.generator,
            )
            if self.rest_refinement
            else None
        )
        # The horizon is a physical-world deadline, not a command-count
        # deadline. Under variable transport one Motor interval can span more
        # than the nominal two physics ticks.
        deadline_ticks = horizon * (PHYSICS_HZ // MOTOR_HZ)
        elapsed_ticks = torch.zeros(BATCH_SIZE, dtype=torch.long)
        next_spine_tick = torch.zeros(BATCH_SIZE, dtype=torch.long)
        spine_stride_ticks = PHYSICS_HZ // SPINE_HZ
        goal = None
        loss = torch.tensor(0.)
        for step in range(horizon):
            active = elapsed_ticks < deadline_ticks
            if not bool(active.any()):
                break

            need_spine = active & (elapsed_ticks >= next_spine_tick)
            if bool(need_spine.any()):
                if cancel is not None and cancel.is_set():
                    return {"updated": False, "reason": "cancelled"}
                mean, _ = model.spine.policy_mean(
                    history, input_delay=input_delay
                )
                new_goal = model.spine.motor_goal(torch.tanh(mean))
                goal = (
                    new_goal
                    if goal is None
                    else torch.where(need_spine.unsqueeze(-1), new_goal, goal)
                )
                next_spine_tick = torch.where(
                    need_spine,
                    elapsed_ticks + spine_stride_ticks,
                    next_spine_tick,
                )
            if goal is None:
                raise RuntimeError("imagined Spine goal was not initialized")

            previous_effort = effort
            candidate_effort = model.deterministic_motor(
                goal, torch.stack((velocity, effort), -1)
            )
            effort = torch.where(active, candidate_effort, previous_effort)
            remaining_ticks = (deadline_ticks - elapsed_ticks).clamp_min(0)

            if self.rest_refinement:
                extra_ticks = delay_schedule[step]
                predicted_dx, predicted_vx, interval_ticks = (
                    self.dynamics.predict_delayed_interval(
                        velocity,
                        previous_effort,
                        effort,
                        extra_ticks,
                        max_ticks=remaining_ticks,
                    )
                )
                input_delay = torch.where(
                    active,
                    extra_ticks.float() / (PHYSICS_HZ // MOTOR_HZ),
                    input_delay,
                )
            else:
                predicted = torch.stack(
                    (velocity, effort, torch.ones_like(velocity)), -1
                ) @ self.dynamics.weights
                interval_ticks = torch.minimum(
                    torch.full_like(elapsed_ticks, PHYSICS_HZ // MOTOR_HZ),
                    remaining_ticks,
                )
                predicted_dx = predicted[:, 0]
                predicted_vx = predicted[:, 1]

            x = x + torch.where(active, predicted_dx, 0.0) * PLAYER_MAX_SPEED
            velocity = torch.where(active, predicted_vx, velocity)
            elapsed_ticks = elapsed_ticks + torch.where(
                active, interval_ticks, torch.zeros_like(interval_ticks)
            )
            history = torch.cat(
                (history[:, :, 1:], frame().unsqueeze(-1)), -1
            )
            error = target - x

            # Integrate state cost over authoritative world time. For nominal
            # 0/1 timing interval_ticks==2, so this is exactly the old 1/horizon
            # weighting. Variable latency no longer secretly lengthens the
            # imagined deadline.
            time_weight = interval_ticks.float() / deadline_ticks
            position_cost = torch.sqrt(error.square() + .01) - .1
            speed_cost = (
                .5
                * (velocity * PLAYER_MAX_SPEED).square()
                / (1 + error.square())
            )
            loss = loss + (position_cost * time_weight).mean()
            loss = loss + (speed_cost * time_weight).mean()
        loss = loss + 2 * (torch.sqrt((target - x).square() + .01) - .1).mean()
        # Position alone can rate a still-moving arrival as an excellent final
        # state. Give terminal rest its own gradient, with useful resolution
        # below one world unit/second, without rounding or snapping an action.
        terminal_speed_weight = 2. if self.rest_refinement else .5
        loss = loss + terminal_speed_weight * (torch.sqrt((velocity * PLAYER_MAX_SPEED).square() + .0025) - .05).mean()
        if not torch.isfinite(loss):
            raise RuntimeError("nonfinite imagined state cost")
        self.optimizer.zero_grad(set_to_none=True)
        loss.backward()
        grad = torch.nn.utils.clip_grad_norm_(model.spine.parameters(), 10., error_if_nonfinite=True)
        for group in self.optimizer.param_groups:
            group["lr"] = .002 if self.updates < 100 else .0005
        self.optimizer.step()
        self.updates += 1
        return {"updated": True, "loss": float(loss.detach()), "grad_norm": float(grad),
                "updates": self.updates, "imagined_seconds": horizon / MOTOR_HZ,
                "rest_refinement": self.rest_refinement,
                "dynamics": dict(self.dynamics.metrics)}

    def validate(self, client, *, player_id: str, cancel=None) -> dict | None:
        cases = []
        suite = (
            [(spawn, target, delay_mode)
             for delay_mode in DELAY_MODES
             for spawn, target in REFINEMENT_CASES]
            if self.rest_refinement
            else [(spawn, target, "0") for spawn, target in VALIDATION_CASES]
        )
        for spawn, target, delay_mode in suite:
            if cancel is not None and cancel.is_set():
                return None
            transport = (
                client
                if delay_mode == "0"
                else DelayedCommands(client, mode=delay_mode)
            )
            result = collect_episode(self.model, transport, player_id=player_id, spawn_x=spawn,
                                     target_x=target, max_seconds=8., sampled=False, cancel=cancel)
            if result.result == "cancelled":
                return None
            if result.result not in {"success", "timeout"}:
                raise RuntimeError(f"invalid validation rollout: {result.result}")
            cases.append({"spawn_x": spawn, "target_x": target,
                          "delay_mode": delay_mode,
                          "seconds": result.evidence["simulation_seconds"],
                          "passed": result.result == "success" and result.evidence["wall_contacts"] == 0,
                          "error": result.final_error, "vx": result.evidence["vx"],
                          "wall_contacts": result.evidence["wall_contacts"]})
        score = (sum(c["passed"] for c in cases),
                 -max(c["seconds"] for c in cases) if self.rest_refinement else 0.,
                 -sum(abs(c["error"]) + abs(c["vx"]) + 1000 * c["wall_contacts"] for c in cases))
        result = {"passed": all(c["passed"] for c in cases), "cases": cases, "update": self.updates}
        if self.best_score is None or score > self.best_score:
            self.best_score = score
            self.best = copy.deepcopy(self.model.state_dict())
            self.best_validation = result
        return result

    def state_dict(self) -> dict:
        return {"algorithm": ALGORITHM, "updates": self.updates,
                "rest_refinement": self.rest_refinement,
                "dynamics": self.dynamics.state_dict(), "optimizer": self.optimizer.state_dict(),
                "generator": self.generator.get_state(), "rng": self.rng.getstate(),
                "exploration_state": self.exploration_state,
                "candidate": copy.deepcopy(self.model.state_dict()),
                "best": self.best, "best_score": self.best_score,
                "best_validation": self.best_validation}

    def restore(self, state: dict) -> None:
        if state.get("algorithm") != ALGORITHM:
            raise ValueError("incompatible Spine school checkpoint; use --fresh")
        self.updates = state["updates"]
        self.rest_refinement = state.get("rest_refinement", False)
        self.dynamics.restore(state["dynamics"])
        self.optimizer.load_state_dict(state["optimizer"])
        self.generator.set_state(state["generator"])
        self.rng.setstate(state["rng"])
        self.exploration_state = state["exploration_state"]
        for candidate in (state["candidate"], state["best"]):
            if candidate is not None and any(
                not torch.equal(value, candidate.get("motor." + name, torch.empty(0)))
                for name, value in self.model.motor.state_dict().items()
            ):
                raise ValueError("school state embeds a different Motor brain")
        self.model.load_state_dict(state["candidate"])
        self.best = state["best"]
        self.best_score = state["best_score"]
        self.best_validation = state["best_validation"]
        if self.best_score is not None and len(self.best_score) != 3:
            self.best_score = None


def train_school(client, *, motor_id: str, episodes: int, seed: int, fresh: bool,
                 player_id: str, target_x: float | None = None, max_seconds: float = 8.,
                 path: Path | None = None, cancel: threading.Event | None = None,
                 on_episode: Callable | None = None, on_rollout: Callable | None = None,
                 final_verify: bool = True) -> dict:
    """Shared operator/MCP training; only the client determines world pacing."""
    if episodes <= 0:
        raise ValueError("episodes must be positive")
    ensure_player(client, player_id)
    model, package = build_spine_policy(motor_id, seed=seed)
    school = SpineSchool(model, seed=seed)
    path = path or checkpoint_path()
    prior = 0
    if not fresh and path.exists():
        extra = load_checkpoint(path, model)
        if extra.get("motor_id") != motor_id:
            raise ValueError("checkpoint Motor identity mismatch")
        if "spine_school" not in extra:
            raise ValueError("checkpoint uses legacy PPO; use --fresh for model-based training")
        school.restore(extra["spine_school"])
        prior = extra["episodes"]
    reward = _prepare_reward_config(RewardStore(), fresh=fresh)
    completed = 0

    def save(*, promote: bool = True, verification=None):
        state = school.state_dict()
        if promote and school.best is not None:
            model.load_state_dict(school.best)
        save_checkpoint(path, model, extra={"episodes": prior + completed, "seed": seed,
                        "algorithm": ALGORITHM, "spine_school": state,
                        "spine_verification": verification,
                        **motor_checkpoint_extra(package)})
        model.load_state_dict(state["candidate"])

    if fresh or not path.exists():
        save()
    # fork_rng prevents a service TRAIN from resetting another model's PRNG.
    with torch.random.fork_rng(devices=[]):
        torch.set_rng_state(school.exploration_state)
        for offset in range(episodes):
            if cancel is not None and cancel.is_set():
                break
            spawn = school.rng.uniform(100., 900.)
            distance = school.rng.choice(SCALES[1:]) * school.rng.uniform(-1, 1)
            target = float(target_x) if target_x is not None else min(980., max(20., spawn + distance))
            # Early/periodic stochastic rollouts identify the body's response;
            # deterministic trials measure the actual policy being deployed.
            exploratory = school.updates < 5 or (prior + offset) % 4 == 0
            result = collect_episode(model, client, player_id=player_id, spawn_x=spawn,
                                     target_x=target, max_seconds=min(max_seconds, 3. if exploratory else 8.),
                                     reward_config=reward, sampled=exploratory, cancel=cancel)
            if result.result == "cancelled":
                break
            if result.result not in {"success", "timeout"}:
                raise RuntimeError(f"invalid training rollout: {result.result}")
            if on_rollout is not None:
                on_rollout(result, policy_id(model), prior + offset + 1)
            school.exploration_state = torch.get_rng_state()
            school.dynamics.add(result.motor_transitions)
            school.dynamics.fit()
            metrics = school.update(cancel=cancel)
            if cancel is None or not cancel.is_set():
                metrics = school.update(cancel=cancel)
            completed += 1
            validation = None
            if school.updates and school.updates % 10 == 0:
                validation = school.validate(client, player_id=player_id, cancel=cancel)
            summary = {"episode": prior + completed, "algorithm": ALGORITHM,
                       "spawn_x": spawn, "target_x": target, "result": result.result,
                       "final_x": result.final_x, "final_error": result.final_error,
                       "reward": result.reward, "motor_steps": result.motor_steps,
                       "controller_requests": result.controller_requests,
                       "loss": metrics.get("loss"), "timing": result.evidence,
                       "diagnostics": metrics, "validation": validation,
                       "best_validation": school.best_validation, "sampled": exploratory}
            if on_episode is not None:
                on_episode(summary)
            if completed % 10 == 0:
                save()
    cancelled = cancel is not None and cancel.is_set()
    if completed and not cancelled and school.updates >= 10 and school.updates % 10 != 0:
        school.validate(client, player_id=player_id, cancel=cancel)
    candidate = copy.deepcopy(model.state_dict())
    if school.best is not None:
        model.load_state_dict(school.best)
    verification = None
    if final_verify and not cancelled:
        verification = verify_spine_policy(model, client, player_id=player_id, target_override=target_x)
        verification["recovery"] = verify_recovery_policy(model, client, player_id=player_id)
        verification["passed"] = verification["passed"] and verification["recovery"]["passed"]
    model.load_state_dict(candidate)
    save(promote=True, verification=verification)
    return {"episodes_completed": completed, "algorithm": ALGORITHM, "cancelled": cancelled,
            "best_validation": school.best_validation, "verification": verification,
            "checkpoint_ready": path.is_file()}


def train_cli(args) -> int:
    from .host import HostClient
    from .unpaced import UnpacedHostClient
    # Tiny CPU networks are dominated by thread-pool overhead otherwise.
    torch.set_num_threads(1)
    client = (UnpacedHostClient("gamelab-school", player_id=args.player)
              if args.mode == "unpaced" else HostClient("gamelab-school"))

    def report(row):
        metrics = row["diagnostics"]
        best = row["best_validation"]
        passes = sum(c["passed"] for c in best["cases"]) if best else 0
        print(f"Episode {row['episode']} algorithm={ALGORITHM} {row['result'].upper()} "
              f"error={row['final_error']:+.3f} updates={metrics.get('updates', 0)} "
              f"loss={metrics.get('loss')} best_validation={passes}/{len(best['cases']) if best else 0}", flush=True)

    try:
        print(f"Spine School algorithm={ALGORITHM} mode={args.mode} motor={args.motor} "
              "objective=smooth_position+near_goal_speed+terminal_position_and_speed", flush=True)
        reward = RewardConfig() if args.fresh else RewardStore().load()
        print("Reward instrumentation " + json.dumps(reward.public(), sort_keys=True), flush=True)
        result = train_school(client, motor_id=args.motor, episodes=args.episodes, seed=args.seed,
                              fresh=args.fresh, player_id=args.player, target_x=args.target,
                              on_episode=report)
        print("SPINE VERIFY " + json.dumps(result["verification"], sort_keys=True), flush=True)
        return 0 if result["verification"]["passed"] else 2
    finally:
        client.close()
