"""The one PPO implementation used for every Game2 training episode."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from typing import Callable

import torch

from game2.v2.contracts.motor import PlanCommand
from game2.v2.learning.vision import vision_to_tensor

from .config import (
    TRAINING_CONTROL_REQUEST_PENALTY,
    PPO_BATCH_SIZE,
    PPO_CLIP_EPS,
    PPO_ENTROPY_COEF,
    PPO_DISCOUNT_TICKS,
    PPO_EPOCHS,
    PPO_GAE_LAMBDA,
    PPO_GAMMA,
    PPO_HISTORY_STRIDE_TICKS,
    PPO_MAX_GRAD_NORM,
    PPO_TAIL_TICKS,
    PPO_VALUE_COEF,
)
from .episode_dataset import EpisodeDataset, EpisodeStep


@dataclass(frozen=True)
class EpisodeTrainingResult:
    updated: bool
    loss: float
    metrics: dict[str, object]


def unique_parameters(*modules) -> list[torch.nn.Parameter]:
    seen: set[int] = set()
    result: list[torch.nn.Parameter] = []
    for module in modules:
        if not isinstance(module, torch.nn.Module):
            continue
        for parameter in module.parameters():
            identity = id(parameter)
            if identity in seen:
                continue
            seen.add(identity)
            result.append(parameter)
    return result


def select_ppo_indexes(
    world_ticks: list[int] | tuple[int, ...],
    finish_world_tick: int,
) -> list[int]:
    if not world_ticks:
        return []
    tail_start = max(world_ticks[0], finish_world_tick - PPO_TAIL_TICKS)
    origin = world_ticks[0]
    history: dict[int, int] = {}
    tail: list[int] = []
    previous = None
    for index, tick in enumerate(world_ticks):
        if previous is not None and tick < previous:
            raise ValueError("episode world ticks cannot move backwards")
        previous = tick
        if tick >= tail_start:
            tail.append(index)
        else:
            history[(tick - origin) // PPO_HISTORY_STRIDE_TICKS] = index
    selected = sorted(set(history.values()) | set(tail))
    if 0 not in selected:
        selected.insert(0, 0)
    last = len(world_ticks) - 1
    if last not in selected:
        selected.append(last)
    return selected


def _distance(step: EpisodeStep) -> float | None:
    if None in (step.self_x, step.self_y, step.goal_x, step.goal_y):
        return None
    return math.hypot(
        float(step.self_x) - float(step.goal_x),
        float(step.self_y) - float(step.goal_y),
    )


def _discount(base: float, elapsed_ticks: int) -> float:
    if elapsed_ticks < 0:
        raise ValueError("elapsed_ticks cannot be negative")
    return float(base) ** (float(elapsed_ticks) / PPO_DISCOUNT_TICKS)


def _terminal_contribution(
    steps: tuple[EpisodeStep, ...],
    terminal_reward: float,
    finish_world_tick: int,
) -> float:
    if not steps:
        return 0.0
    delay = max(0, finish_world_tick - 1 - steps[-1].world_tick)
    return float(terminal_reward) * _discount(PPO_GAMMA, delay)


def _rewards(
    steps: tuple[EpisodeStep, ...],
    terminal_reward: float,
    finish_world_tick: int,
) -> list[float]:
    rewards = [
        -TRAINING_CONTROL_REQUEST_PENALTY * int(step.control_requested)
        for step in steps
    ]
    distances = [_distance(step) for step in steps]
    start_distance = next(
        (value for value in distances if value is not None and value > 0),
        None,
    )
    if start_distance is not None:
        for index in range(len(steps) - 1):
            before = distances[index]
            after = distances[index + 1]
            if before is not None and after is not None:
                rewards[index] += (before - after) / start_distance
    if steps:
        rewards[-1] += _terminal_contribution(
            steps, terminal_reward, finish_world_tick
        )
    return rewards


def _gae(
    steps: tuple[EpisodeStep, ...],
    rewards: list[float],
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    values = [step.old_value for step in steps]
    raw = [0.0] * len(steps)
    gae = 0.0
    for index in range(len(steps) - 1, -1, -1):
        if index + 1 < len(steps):
            delta_ticks = steps[index + 1].world_tick - steps[index].world_tick
            if delta_ticks < 0:
                raise ValueError("episode world ticks cannot move backwards")
            gamma = _discount(PPO_GAMMA, delta_ticks)
            trace = _discount(PPO_GAMMA * PPO_GAE_LAMBDA, delta_ticks)
            next_value = values[index + 1]
        else:
            gamma = 0.0
            trace = 0.0
            next_value = 0.0
        delta = rewards[index] + gamma * next_value - values[index]
        gae = delta + trace * gae
        raw[index] = gae
    raw_tensor = torch.tensor(raw, dtype=torch.float32)
    returns = raw_tensor + torch.tensor(values, dtype=torch.float32)
    advantages = raw_tensor.clone()
    if len(steps) > 1:
        std = advantages.std(unbiased=False)
        if float(std) > 1e-8:
            advantages = (advantages - advantages.mean()) / (std + 1e-8)
    return raw_tensor, advantages, returns


def _parameter_stats(
    parameters: list[torch.nn.Parameter],
) -> tuple[float, str]:
    squared_norm = 0.0
    digest = hashlib.sha256()
    for parameter in parameters:
        tensor = parameter.detach().cpu().contiguous()
        squared_norm += float(torch.sum(tensor.float() * tensor.float()))
        digest.update(str(tuple(tensor.shape)).encode("ascii"))
        digest.update(str(tensor.dtype).encode("ascii"))
        digest.update(tensor.numpy().tobytes())
    return math.sqrt(squared_norm), digest.hexdigest()


def train_episode(
    model,
    dataset: EpisodeDataset,
    *,
    should_stop: Callable[[], bool] | None = None,
    on_progress: Callable[[dict[str, object]], None] | None = None,
) -> EpisodeTrainingResult:
    meta = dataset.metadata()
    all_steps = dataset.steps()
    # KEEP and redundant commands are real policy choices without a wire action.
    # A state-changing realtime command is usable only after its actuation ACK.
    steps = tuple(step for step in all_steps if (
        step.world_tick < int(meta.get("finish_world_tick") or 0)
        and (
            meta["source"] != "realtime"
            or step.actuated
            or step.control_requested
            or (
                step.desired_right == step.pad_right
                and step.desired_jump == step.pad_jump
            )
        )
    ))
    discarded = len(all_steps) - len(steps)
    if (
        meta["mode"] != "train"
        or not bool(meta.get("trainable"))
        or not steps
    ):
        metrics = {
            "rollout_records": len(all_steps),
            "discarded_records": discarded,
            "ppo_records": 0,
            "optimizer_steps": 0,
            "reward_sum": 0.0,
            "controller_requests": sum(
                int(step.control_requested) for step in all_steps
            ),
            "controller_accepted": sum(
                int(step.control_status == "accepted") for step in all_steps
            ),
            "controller_rejected": sum(
                int(step.control_status == "rejected") for step in all_steps
            ),
            "controller_duplicate": sum(
                int(step.control_status == "duplicate") for step in all_steps
            ),
        }
        dataset.write_training_annotations(
            [], updated=False, loss=0.0, metrics=metrics
        )
        return EpisodeTrainingResult(False, 0.0, metrics)

    if model.optimizer is None:
        raise RuntimeError("episode PPO requires a trainable optimizer")
    finish_tick = int(meta["finish_world_tick"])
    terminal_reward = float(meta["terminal_reward"])
    rewards = _rewards(steps, terminal_reward, finish_tick)
    raw_gae, advantages_all, returns_all = _gae(steps, rewards)
    selected_indexes = select_ppo_indexes(
        [step.world_tick for step in steps],
        finish_tick,
    )
    selected_steps = tuple(steps[index] for index in selected_indexes)
    selected_tensor = torch.tensor(selected_indexes, dtype=torch.long)
    advantages = advantages_all[selected_tensor]
    returns = returns_all[selected_tensor]

    old_log_prob = torch.tensor(
        [step.old_log_prob for step in selected_steps], dtype=torch.float32
    )
    actions = torch.tensor(
        [[int(step.action_right), int(step.action_jump)] for step in selected_steps],
        dtype=torch.long,
    )
    skill_active = torch.tensor(
        [
            [step.skill_right_active, step.skill_jump_active]
            for step in selected_steps
        ],
        dtype=torch.float32,
    )
    planner_decision = torch.tensor(
        [step.planner_decision for step in selected_steps],
        dtype=torch.float32,
    )
    plan_commands = torch.tensor(
        [int(step.plan_command) for step in selected_steps],
        dtype=torch.long,
    )
    sequence_lookup = {
        step.policy_sequence: index
        for index, step in enumerate(selected_steps)
    }
    plan_source_indexes_list = []
    for step in selected_steps:
        source = sequence_lookup.get(step.plan_policy_sequence)
        if source is None:
            raise ValueError(
                "PPO selection lost the Planner decision for a latched MotorPlan"
            )
        plan_source_indexes_list.append(source)
    plan_source_indexes = torch.tensor(
        plan_source_indexes_list, dtype=torch.long
    )
    parameters = unique_parameters(
        model.planner, model.motor_controller, model.critic
    )
    norm_before, hash_before = _parameter_stats(parameters)
    generator = torch.Generator(device="cpu")
    generator.manual_seed(int(meta["seed"]))

    shared = (
        hasattr(model.planner, "backbone")
        and hasattr(model.planner, "encode_prepared")
        and hasattr(model.planner, "forward_features")
        and hasattr(model.critic, "forward_features")
        and model.planner.backbone is getattr(model.critic, "backbone", None)
    )
    if shared:
        prepared_vision = torch.stack([
            model.planner.backbone.prepare(
                vision_to_tensor(step.vision_grid).unsqueeze(0)
            )[0]
            for step in selected_steps
        ])
        full_vision = None
    else:
        prepared_vision = None
        full_vision = torch.stack([
            vision_to_tensor(step.vision_grid) for step in selected_steps
        ])
    velocity_all = torch.tensor(
        [[step.velocity_x, step.velocity_y] for step in selected_steps],
        dtype=torch.float32,
    )
    grounded_all = torch.tensor(
        [step.grounded for step in selected_steps],
        dtype=torch.float32,
    )
    sensor_pad_all = torch.tensor(
        [
            [step.sensor_right_pressed, step.sensor_jump_pressed]
            for step in selected_steps
        ],
        dtype=torch.float32,
    )
    body_state_all = body_state_batch(
        velocity_all, grounded_all, sensor_pad_all
    )
    planner_state_all = torch.tensor(
        [
            [
                step.planner_input_goal_dx,
                step.planner_input_goal_dy,
                step.planner_input_right_active,
                step.planner_input_jump_active,
            ]
            for step in selected_steps
        ],
        dtype=torch.float32,
    )

    count = len(selected_steps)
    batches_per_epoch = (count + PPO_BATCH_SIZE - 1) // PPO_BATCH_SIZE
    total_updates = PPO_EPOCHS * batches_per_epoch
    final_log_prob = torch.empty(count, dtype=torch.float32)
    final_values = torch.empty(count, dtype=torch.float32)
    final_probabilities = torch.empty((count, 2, 3), dtype=torch.float32)
    final_plan_probabilities = torch.empty((count, 3), dtype=torch.float32)
    final_skill_probabilities = torch.empty((count, 2), dtype=torch.float32)
    total_loss = total_policy_loss = total_value_loss = 0.0
    total_entropy = total_grad_norm = 0.0
    total_right_confidence = total_jump_confidence = 0.0
    total_examples = updates = 0

    for epoch in range(PPO_EPOCHS):
        if should_stop is not None and should_stop():
            raise KeyboardInterrupt
        order = torch.randperm(count, generator=generator)
        for batch_number, start in enumerate(
            range(0, count, PPO_BATCH_SIZE), start=1
        ):
            if should_stop is not None and should_stop():
                raise KeyboardInterrupt
            indexes = order[start:start + PPO_BATCH_SIZE]
            source_indexes = plan_source_indexes[indexes]
            if shared:
                assert prepared_vision is not None
                current_features = model.planner.encode_prepared(
                    prepared_vision[indexes]
                )
                plan_features = model.planner.encode_prepared(
                    prepared_vision[source_indexes]
                )
                current_plan_state = planner_state_all[indexes].to(
                    dtype=current_features.dtype,
                    device=current_features.device,
                )
                source_plan_state = planner_state_all[source_indexes].to(
                    dtype=plan_features.dtype,
                    device=plan_features.device,
                )
                current_planner_output = model.planner.forward_features(
                    current_features, current_plan_state
                )
                plan_planner_output = model.planner.forward_features(
                    plan_features, source_plan_state
                )
                current_body_state = body_state_all[indexes].to(
                    dtype=current_features.dtype,
                    device=current_features.device,
                )
                critic_context = critic_context_batch(
                    current_body_state, current_plan_state
                )
                values = model.critic.forward_features(
                    current_features, critic_context
                )
            else:
                assert full_vision is not None
                current_plan_state = planner_state_all[indexes].to(
                    dtype=full_vision.dtype,
                    device=full_vision.device,
                )
                source_plan_state = planner_state_all[source_indexes].to(
                    dtype=full_vision.dtype,
                    device=full_vision.device,
                )
                current_planner_output = model.planner(
                    full_vision[indexes], current_plan_state
                )
                plan_planner_output = model.planner(
                    full_vision[source_indexes], source_plan_state
                )
                current_body_state = body_state_all[indexes].to(
                    dtype=full_vision.dtype,
                    device=full_vision.device,
                )
                critic_context = critic_context_batch(
                    current_body_state, current_plan_state
                )
                values = model.critic(
                    full_vision[indexes], critic_context
                )
            if (
                current_planner_output.ndim != 2
                or current_planner_output.shape[1] != 7
                or plan_planner_output.ndim != 2
                or plan_planner_output.shape[1] != 7
            ):
                raise ValueError(
                    "Planner must return goal[2] + plan_command_logits[3] "
                    "+ skill_logits[2]"
                )
            goals = plan_planner_output[:, :2]
            plan_command_logits = current_planner_output[:, 2:5]
            skill_logits = current_planner_output[:, 5:7]
            velocity = velocity_all[indexes].to(
                dtype=goals.dtype, device=goals.device
            )
            grounded = grounded_all[indexes].to(
                dtype=goals.dtype, device=goals.device
            )
            sensor_pad = sensor_pad_all[indexes].to(
                dtype=goals.dtype, device=goals.device
            )
            logits = model.motor_controller.forward_batch(
                goals, velocity, grounded, sensor_pad
            )
            if logits.ndim != 2 or logits.shape[1] != 6:
                raise ValueError("Motor Controller must return six command logits")
            command_logits = logits.reshape(-1, 2, 3)
            batch_actions = actions[indexes].to(device=logits.device)
            motor_log_probabilities = torch.log_softmax(command_logits, dim=2)
            motor_selected_log_prob = motor_log_probabilities.gather(
                2, batch_actions.unsqueeze(2)
            ).squeeze(2)
            batch_skills = skill_active[indexes].to(device=logits.device)
            batch_planner = planner_decision[indexes].to(device=logits.device)
            batch_plan_commands = plan_commands[indexes].to(
                device=logits.device
            )
            batch_set_plan = (
                (batch_plan_commands == int(PlanCommand.SET)).to(
                    dtype=logits.dtype
                ) * batch_planner
            )
            plan_log_probabilities = torch.log_softmax(
                plan_command_logits, dim=1
            )
            plan_selected_log_prob = plan_log_probabilities.gather(
                1, batch_plan_commands.unsqueeze(1)
            ).squeeze(1)
            skill_log_probabilities = -torch.nn.functional.binary_cross_entropy_with_logits(
                skill_logits,
                batch_skills,
                reduction="none",
            )
            new_log_prob = (
                plan_selected_log_prob * batch_planner
                + skill_log_probabilities.sum(dim=1) * batch_set_plan
                + (motor_selected_log_prob * batch_skills).sum(dim=1)
            )
            batch_old = old_log_prob[indexes].to(logits.device)
            batch_adv = advantages[indexes].to(logits.device)
            ratio = torch.exp(new_log_prob - batch_old)
            unclipped = ratio * batch_adv
            clipped = torch.clamp(
                ratio, 1.0 - PPO_CLIP_EPS, 1.0 + PPO_CLIP_EPS
            ) * batch_adv
            policy_loss = -torch.minimum(unclipped, clipped).mean()
            value_loss = torch.nn.functional.mse_loss(
                values, returns[indexes].to(values.device)
            )
            probabilities = torch.softmax(command_logits, dim=2)
            motor_entropy = -(
                probabilities * motor_log_probabilities
            ).sum(dim=2)
            plan_probabilities = torch.softmax(
                plan_command_logits, dim=1
            )
            plan_entropy = -(
                plan_probabilities
                * torch.log(plan_probabilities.clamp_min(1e-8))
            ).sum(dim=1)
            skill_probabilities = torch.sigmoid(skill_logits)
            skill_entropy = -(
                skill_probabilities * torch.log(skill_probabilities.clamp_min(1e-8))
                + (1.0 - skill_probabilities)
                * torch.log((1.0 - skill_probabilities).clamp_min(1e-8))
            )
            entropy = (
                plan_entropy * batch_planner
                + skill_entropy.sum(dim=1) * batch_set_plan
                + (motor_entropy * batch_skills).sum(dim=1)
            ).mean()
            loss = (
                policy_loss
                + PPO_VALUE_COEF * value_loss
                - PPO_ENTROPY_COEF * entropy
            )
            model.optimizer.zero_grad(set_to_none=True)
            loss.backward()
            grad_norm = torch.nn.utils.clip_grad_norm_(
                parameters, max_norm=PPO_MAX_GRAD_NORM
            )
            model.optimizer.step()

            batch_count = len(indexes)
            total_loss += float(loss.detach())
            total_policy_loss += float(policy_loss.detach())
            total_value_loss += float(value_loss.detach())
            total_entropy += float(entropy.detach())
            total_grad_norm += float(grad_norm)
            total_right_confidence += float(
                probabilities[:, 0, :].detach().amax(dim=1).sum()
            )
            total_jump_confidence += float(
                probabilities[:, 1, :].detach().amax(dim=1).sum()
            )
            total_examples += batch_count
            updates += 1
            if on_progress is not None:
                on_progress({
                    "epoch": epoch + 1,
                    "epochs": PPO_EPOCHS,
                    "batch": batch_number,
                    "batches": batches_per_epoch,
                    "step": updates,
                    "steps": total_updates,
                    "loss": float(loss.detach()),
                    "rollout_records": len(steps),
                    "ppo_records": count,
                })

    # Re-evaluate once after every Adam step for true post-update diagnostics.
    with torch.no_grad():
        for start in range(0, count, PPO_BATCH_SIZE):
            indexes = torch.arange(start, min(start + PPO_BATCH_SIZE, count))
            source_indexes = plan_source_indexes[indexes]
            if shared:
                assert prepared_vision is not None
                current_features = model.planner.encode_prepared(
                    prepared_vision[indexes]
                )
                plan_features = model.planner.encode_prepared(
                    prepared_vision[source_indexes]
                )
                current_plan_state = planner_state_all[indexes].to(
                    dtype=current_features.dtype,
                    device=current_features.device,
                )
                source_plan_state = planner_state_all[source_indexes].to(
                    dtype=plan_features.dtype,
                    device=plan_features.device,
                )
                current_planner_output = model.planner.forward_features(
                    current_features, current_plan_state
                )
                plan_planner_output = model.planner.forward_features(
                    plan_features, source_plan_state
                )
                current_body_state = body_state_all[indexes].to(
                    dtype=current_features.dtype,
                    device=current_features.device,
                )
                critic_context = critic_context_batch(
                    current_body_state, current_plan_state
                )
                values = model.critic.forward_features(
                    current_features, critic_context
                )
            else:
                assert full_vision is not None
                current_plan_state = planner_state_all[indexes].to(
                    dtype=full_vision.dtype,
                    device=full_vision.device,
                )
                source_plan_state = planner_state_all[source_indexes].to(
                    dtype=full_vision.dtype,
                    device=full_vision.device,
                )
                current_planner_output = model.planner(
                    full_vision[indexes], current_plan_state
                )
                plan_planner_output = model.planner(
                    full_vision[source_indexes], source_plan_state
                )
                current_body_state = body_state_all[indexes].to(
                    dtype=full_vision.dtype,
                    device=full_vision.device,
                )
                critic_context = critic_context_batch(
                    current_body_state, current_plan_state
                )
                values = model.critic(
                    full_vision[indexes], critic_context
                )
            goals = plan_planner_output[:, :2]
            plan_command_logits = current_planner_output[:, 2:5]
            skill_logits = current_planner_output[:, 5:7]
            velocity = velocity_all[indexes].to(
                dtype=goals.dtype, device=goals.device
            )
            grounded = grounded_all[indexes].to(
                dtype=goals.dtype, device=goals.device
            )
            sensor_pad = sensor_pad_all[indexes].to(
                dtype=goals.dtype, device=goals.device
            )
            logits = model.motor_controller.forward_batch(
                goals, velocity, grounded, sensor_pad
            )
            command_logits = logits.reshape(-1, 2, 3)
            probabilities = torch.softmax(command_logits, dim=2)
            motor_log_probabilities = torch.log_softmax(command_logits, dim=2)
            batch_actions = actions[indexes].to(device=logits.device)
            batch_skills = skill_active[indexes].to(device=logits.device)
            batch_planner = planner_decision[indexes].to(device=logits.device)
            batch_plan_commands = plan_commands[indexes].to(
                device=logits.device
            )
            batch_set_plan = (
                (batch_plan_commands == int(PlanCommand.SET)).to(
                    dtype=logits.dtype
                ) * batch_planner
            )
            motor_selected_log_prob = motor_log_probabilities.gather(
                2, batch_actions.unsqueeze(2)
            ).squeeze(2)
            plan_log_probabilities = torch.log_softmax(
                plan_command_logits, dim=1
            )
            plan_selected_log_prob = plan_log_probabilities.gather(
                1, batch_plan_commands.unsqueeze(1)
            ).squeeze(1)
            skill_log_probabilities = -torch.nn.functional.binary_cross_entropy_with_logits(
                skill_logits,
                batch_skills,
                reduction="none",
            )
            final_lp = (
                plan_selected_log_prob * batch_planner
                + skill_log_probabilities.sum(dim=1) * batch_set_plan
                + (motor_selected_log_prob * batch_skills).sum(dim=1)
            )
            final_log_prob[indexes] = final_lp.detach().cpu()
            final_values[indexes] = values.detach().cpu()
            final_probabilities[indexes] = probabilities.detach().cpu()
            final_plan_probabilities[indexes] = torch.softmax(
                plan_command_logits, dim=1
            ).detach().cpu()
            final_skill_probabilities[indexes] = torch.sigmoid(
                skill_logits
            ).detach().cpu()

    norm_after, hash_after = _parameter_stats(parameters)
    loss_value = total_loss / max(updates, 1)

    final_log_ratio = final_log_prob - old_log_prob
    diagnostic_log_ratio = final_log_ratio.clamp(-20.0, 20.0)
    diagnostic_ratio = torch.exp(diagnostic_log_ratio)
    approx_kl = max(
        0.0,
        float(
            (
                (diagnostic_ratio - 1.0)
                - diagnostic_log_ratio
            ).mean()
        ),
    )
    lower_log_clip = math.log(1.0 - PPO_CLIP_EPS)
    upper_log_clip = math.log(1.0 + PPO_CLIP_EPS)
    clip_fraction = float(
        (
            (final_log_ratio < lower_log_clip)
            | (final_log_ratio > upper_log_clip)
        ).float().mean()
    )
    returns_cpu = returns.detach().cpu()
    residual = returns_cpu - final_values
    return_variance = float(returns_cpu.var(unbiased=False))
    critic_explained_variance = (
        1.0 - float(residual.var(unbiased=False)) / return_variance
        if return_variance > 1e-8 else 0.0
    )
    critic_value_mae = float(residual.abs().mean())

    total_control_requests = sum(int(step.control_requested) for step in steps)
    total_control_penalty = (
        TRAINING_CONTROL_REQUEST_PENALTY * total_control_requests
    )
    terminal_contribution = _terminal_contribution(
        steps, terminal_reward, finish_tick
    )
    progress_reward_sum = (
        float(sum(rewards))
        + total_control_penalty
        - terminal_contribution
    )
    accepted_button_changes = sum(
        int(step.desired_right != step.pad_right)
        + int(step.desired_jump != step.pad_jump)
        for step in steps
        if step.control_status == "accepted"
    )
    total_duration = sum(max(1, int(step.duration_ticks)) for step in steps)

    def effective_state(step: EpisodeStep, button: str) -> bool:
        before = bool(getattr(step, f"pad_{button}"))
        desired = bool(getattr(step, f"desired_{button}"))
        if not step.control_requested:
            return before
        return desired if step.control_status == "accepted" else before

    right_hold_ticks = sum(
        max(1, int(step.duration_ticks))
        for step in steps if effective_state(step, "right")
    )
    jump_hold_ticks = sum(
        max(1, int(step.duration_ticks))
        for step in steps if effective_state(step, "jump")
    )
    metrics: dict[str, object] = {
        "rollout_records": len(all_steps),
        "discarded_records": discarded,
        "ppo_records": count,
        "optimizer_steps": updates,
        "planner_decisions": sum(
            int(step.planner_decision) for step in steps
        ),
        "planner_keep_decisions": sum(
            int(
                step.planner_decision
                and step.plan_command is PlanCommand.KEEP
            )
            for step in steps
        ),
        "planner_set_decisions": sum(
            int(
                step.planner_decision
                and step.plan_command is PlanCommand.SET
            )
            for step in steps
        ),
        "planner_stop_decisions": sum(
            int(
                step.planner_decision
                and step.plan_command is PlanCommand.STOP
            )
            for step in steps
        ),
        "motor_decisions": len(steps),
        "reward_sum": float(sum(rewards)),
        "controller_requests": total_control_requests,
        "controller_accepted": sum(
            int(step.control_status == "accepted") for step in steps
        ),
        "controller_rejected": sum(
            int(step.control_status == "rejected") for step in steps
        ),
        "controller_duplicate": sum(
            int(step.control_status == "duplicate") for step in steps
        ),
        "controller_penalty_sum": -float(total_control_penalty),
        "controller_request_penalty": TRAINING_CONTROL_REQUEST_PENALTY,
        "progress_reward_sum": progress_reward_sum,
        "terminal_reward_contribution": terminal_contribution,
        "task_reward_sum": progress_reward_sum + terminal_contribution,
        "accepted_button_changes": accepted_button_changes,
        "suppressed_button_commands": sum(
            len(tuple(filter(None, step.suppressed_buttons.split(","))))
            for step in steps
        ),
        "right_hold_fraction": right_hold_ticks / max(total_duration, 1),
        "jump_hold_fraction": jump_hold_ticks / max(total_duration, 1),
        "policy_loss": total_policy_loss / max(updates, 1),
        "value_loss": total_value_loss / max(updates, 1),
        "entropy": total_entropy / max(updates, 1),
        "approx_kl": approx_kl,
        "clip_fraction": clip_fraction,
        "critic_explained_variance": critic_explained_variance,
        "critic_value_mae": critic_value_mae,
        "discount_ticks": PPO_DISCOUNT_TICKS,
        "grad_norm": total_grad_norm / max(updates, 1),
        "mean_right_command_confidence": (
            total_right_confidence / max(total_examples, 1)
        ),
        "mean_jump_command_confidence": (
            total_jump_confidence / max(total_examples, 1)
        ),
        "parameter_norm_before": norm_before,
        "parameter_norm_after": norm_after,
        "parameter_hash_before": hash_before,
        "parameter_hash_after": hash_after,
    }

    selected_lookup = {
        global_index: local_index
        for local_index, global_index in enumerate(selected_indexes)
    }
    annotations = []
    for index, step in enumerate(steps):
        local = selected_lookup.get(index)
        new_lp = new_value = ratio_value = None
        new_right = new_jump = (None, None, None)
        new_plan = (None, None, None)
        new_skill_right = new_skill_jump = None
        if local is not None:
            new_lp = float(final_log_prob[local])
            new_value = float(final_values[local])
            ratio_value = math.exp(new_lp - step.old_log_prob)
            new_right = tuple(
                float(value) for value in final_probabilities[local, 0]
            )
            new_jump = tuple(
                float(value) for value in final_probabilities[local, 1]
            )
            if step.planner_decision:
                new_plan = tuple(
                    float(value)
                    for value in final_plan_probabilities[local]
                )
            new_skill_right = float(final_skill_probabilities[local, 0])
            new_skill_jump = float(final_skill_probabilities[local, 1])
        annotations.append({
            "id": step.id,
            "reward": float(rewards[index]),
            "gae": float(raw_gae[index]),
            "advantage": float(advantages_all[index]),
            "return_value": float(returns_all[index]),
            "ppo_selected": local is not None,
            "new_log_prob": new_lp,
            "new_value": new_value,
            "ratio": ratio_value,
            "new_prob_right_keep": new_right[0],
            "new_prob_right_press": new_right[1],
            "new_prob_right_release": new_right[2],
            "new_prob_jump_keep": new_jump[0],
            "new_prob_jump_press": new_jump[1],
            "new_prob_jump_release": new_jump[2],
            "new_prob_plan_keep": new_plan[0],
            "new_prob_plan_set": new_plan[1],
            "new_prob_plan_stop": new_plan[2],
            "new_skill_right_probability": new_skill_right,
            "new_skill_jump_probability": new_skill_jump,
        })

    dataset.write_training_annotations(
        annotations,
        updated=True,
        loss=loss_value,
        metrics=metrics,
    )
    return EpisodeTrainingResult(True, loss_value, metrics)


__all__ = [
    "EpisodeTrainingResult",
    "select_ppo_indexes",
    "train_episode",
    "unique_parameters",
]
