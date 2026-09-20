"""The one PPO implementation used for every Game2 training episode."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from typing import Callable

import torch

from game2.v2.player.learned.contracts import ButtonCommand
from game2.v2.player.learned.vision import vision_to_tensor

from .config import (
    CONTROL_CHANGE_PENALTY,
    PPO_BATCH_SIZE,
    PPO_CLIP_EPS,
    PPO_ENTROPY_COEF,
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


def _rewards(
    steps: tuple[EpisodeStep, ...],
    terminal_reward: float,
    finish_world_tick: int,
) -> list[float]:
    rewards = [
        -CONTROL_CHANGE_PENALTY * (
            int(step.action_right is not ButtonCommand.KEEP)
            + int(step.action_jump is not ButtonCommand.KEEP)
        )
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
        delay = max(0, finish_world_tick - 1 - steps[-1].world_tick)
        rewards[-1] += float(terminal_reward) * (PPO_GAMMA ** delay)
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
            gamma = PPO_GAMMA ** delta_ticks
            trace = (PPO_GAMMA * PPO_GAE_LAMBDA) ** delta_ticks
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
    steps = dataset.steps()
    if (
        meta["mode"] != "train"
        or not bool(meta.get("trainable"))
        or not steps
    ):
        metrics = {
            "rollout_records": len(steps),
            "ppo_records": 0,
            "optimizer_steps": 0,
            "reward_sum": 0.0,
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
    pad_all = torch.tensor(
        [[step.pad_right, step.pad_jump] for step in selected_steps],
        dtype=torch.float32,
    )

    count = len(selected_steps)
    batches_per_epoch = (count + PPO_BATCH_SIZE - 1) // PPO_BATCH_SIZE
    total_updates = PPO_EPOCHS * batches_per_epoch
    final_log_prob = torch.empty(count, dtype=torch.float32)
    final_values = torch.empty(count, dtype=torch.float32)
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
            if shared:
                assert prepared_vision is not None
                features = model.planner.encode_prepared(prepared_vision[indexes])
                goals = model.planner.forward_features(features)
                values = model.critic.forward_features(features)
            else:
                assert full_vision is not None
                vision = full_vision[indexes]
                goals = model.planner(vision)
                values = model.critic(vision)
            pad = pad_all[indexes].to(dtype=goals.dtype, device=goals.device)
            logits = model.motor_controller.forward_batch(goals, pad)
            if logits.ndim != 2 or logits.shape[1] != 6:
                raise ValueError("Motor Controller must return six command logits")
            command_logits = logits.reshape(-1, 2, 3)
            batch_actions = actions[indexes].to(device=logits.device)
            log_probabilities = torch.log_softmax(command_logits, dim=2)
            new_log_prob = log_probabilities.gather(
                2, batch_actions.unsqueeze(2)
            ).squeeze(2).sum(dim=1)
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
            entropy = -(
                probabilities * log_probabilities
            ).sum(dim=2).sum(dim=1).mean()
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

            if epoch == PPO_EPOCHS - 1:
                final_log_prob[indexes] = new_log_prob.detach().cpu()
                final_values[indexes] = values.detach().cpu()
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

    norm_after, hash_after = _parameter_stats(parameters)
    loss_value = total_loss / max(updates, 1)
    metrics: dict[str, object] = {
        "rollout_records": len(steps),
        "ppo_records": count,
        "optimizer_steps": updates,
        "reward_sum": float(sum(rewards)),
        "policy_loss": total_policy_loss / max(updates, 1),
        "value_loss": total_value_loss / max(updates, 1),
        "entropy": total_entropy / max(updates, 1),
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
        if local is not None:
            new_lp = float(final_log_prob[local])
            new_value = float(final_values[local])
            ratio_value = math.exp(new_lp - step.old_log_prob)
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
