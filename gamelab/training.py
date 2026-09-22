"""Joint PPO training for learned Spine CNN + one Motor MLP."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import math
from pathlib import Path
import random
import time

import torch
from torch.distributions import Categorical
from torch.nn import functional as F

from .config import (
    MOTOR_HZ,
    PPO_BATCH_SIZE,
    PPO_CLIP_EPS,
    PPO_ENTROPY_COEF,
    PPO_EPOCHS,
    PPO_GAE_LAMBDA,
    PPO_GAMMA,
    PPO_LEARNING_RATE,
    PPO_MAX_GRAD_NORM,
    PPO_VALUE_COEF,
    SPINE_PERIOD_MOTOR_STEPS,
    SUCCESS_HOLD_STEPS,
    SUCCESS_TOLERANCE,
    TRAIN_EPISODE_SECONDS,
    WORLD_MAX_X,
)
from .host import HostClient, player_from_state
from .models import (
    SensorHistory,
    SpineMotorPolicy,
    load_checkpoint,
    motor_state,
    save_checkpoint,
    sensor_frame,
)
from .runtime import checkpoint_path, reset_player


@dataclass
class Transition:
    history: torch.Tensor
    proprioception: torch.Tensor
    action: int
    old_log_prob: float
    old_value: float
    reward: float
    done: bool


@dataclass
class EpisodeResult:
    target_x: float
    result: str
    final_x: float
    final_error: float
    reward: float
    motor_steps: int
    controller_requests: int
    transitions: list[Transition]


def collect_episode(
    model: SpineMotorPolicy,
    client: HostClient,
    *,
    player_id: str,
    target_x: float,
    max_seconds: float = TRAIN_EPISODE_SECONDS,
) -> EpisodeResult:
    model.eval()
    state = reset_player(client, player_id)
    player = player_from_state(state)
    history = SensorHistory(
        sensor_frame(
            x=player["x"],
            vx=player["vx"],
            move_x=player["move_x"],
            target_x=target_x,
        )
    )

    transitions: list[Transition] = []
    latched_history = history.tensor()
    start = time.monotonic()
    deadline = start
    step = 0
    hold = 0
    total_reward = 0.0
    requests = 0
    final_player = player

    try:
        while True:
            player = player_from_state(state)
            frame = sensor_frame(
                x=player["x"],
                vx=player["vx"],
                move_x=player["move_x"],
                target_x=target_x,
            )
            history.push(frame)
            if step % SPINE_PERIOD_MOTOR_STEPS == 0:
                latched_history = history.tensor().clone()

            proprioception = motor_state(
                vx=player["vx"],
                move_x=player["move_x"],
            )
            with torch.no_grad():
                goal, hidden = model.spine(latched_history)
                logits = model.motor(goal, proprioception)
                value = model.critic(hidden, proprioception)
                distribution = Categorical(logits=logits)
                action_tensor = distribution.sample()
                action = int(action_tensor.item())
                old_log_prob = float(distribution.log_prob(action_tensor).item())
                old_value = float(value.item())

            move_x = model.action_to_move(action)
            if move_x != int(player["move_x"]):
                client.input(move_x)
                requests += 1

            before_distance = abs(float(target_x) - float(player["x"]))

            step += 1
            deadline += 1.0 / MOTOR_HZ
            delay = deadline - time.monotonic()
            if delay > 0:
                time.sleep(delay)

            next_state = client.state()
            next_player = player_from_state(next_state)
            after_distance = abs(float(target_x) - float(next_player["x"]))

            reward = (before_distance - after_distance) / WORLD_MAX_X
            reward -= 0.0005

            if (
                after_distance <= SUCCESS_TOLERANCE
                and abs(float(next_player["vx"])) < 1e-9
                and int(next_player["move_x"]) == 0
            ):
                hold += 1
            else:
                hold = 0

            result = "running"
            done = False
            if hold >= SUCCESS_HOLD_STEPS:
                reward += 1.0
                result = "success"
                done = True
            elif time.monotonic() - start >= max_seconds:
                reward -= 0.25
                result = "timeout"
                done = True

            transitions.append(
                Transition(
                    history=latched_history.clone(),
                    proprioception=proprioception.clone(),
                    action=action,
                    old_log_prob=old_log_prob,
                    old_value=old_value,
                    reward=float(reward),
                    done=done,
                )
            )
            total_reward += reward
            final_player = next_player
            state = next_state

            if done:
                return EpisodeResult(
                    target_x=float(target_x),
                    result=result,
                    final_x=float(final_player["x"]),
                    final_error=float(target_x) - float(final_player["x"]),
                    reward=float(total_reward),
                    motor_steps=step,
                    controller_requests=requests,
                    transitions=transitions,
                )
    finally:
        # Episode-boundary safety only; never contributes to success/reward.
        try:
            state = client.state()
            player = player_from_state(state)
            if int(player["move_x"]) != 0:
                client.input(0)
        except Exception:
            pass


def _advantages(transitions: list[Transition]) -> tuple[torch.Tensor, torch.Tensor]:
    rewards = [item.reward for item in transitions]
    values = [item.old_value for item in transitions]
    advantages = [0.0] * len(transitions)
    gae = 0.0
    next_value = 0.0
    for index in range(len(transitions) - 1, -1, -1):
        mask = 0.0 if transitions[index].done else 1.0
        delta = rewards[index] + PPO_GAMMA * next_value * mask - values[index]
        gae = delta + PPO_GAMMA * PPO_GAE_LAMBDA * mask * gae
        advantages[index] = gae
        next_value = values[index]
    advantage_tensor = torch.tensor(advantages, dtype=torch.float32)
    returns = advantage_tensor + torch.tensor(values, dtype=torch.float32)
    if len(advantages) > 1:
        std = advantage_tensor.std(unbiased=False)
        if float(std) > 1e-8:
            advantage_tensor = (
                advantage_tensor - advantage_tensor.mean()
            ) / (std + 1e-8)
    return advantage_tensor, returns


def ppo_update(
    model: SpineMotorPolicy,
    optimizer: torch.optim.Optimizer,
    transitions: list[Transition],
) -> dict[str, float]:
    if not transitions:
        return {"loss": 0.0, "policy_loss": 0.0, "value_loss": 0.0, "entropy": 0.0}

    histories = torch.stack([item.history for item in transitions])
    proprioception = torch.stack([item.proprioception for item in transitions])
    actions = torch.tensor([item.action for item in transitions], dtype=torch.long)
    old_log_probs = torch.tensor(
        [item.old_log_prob for item in transitions],
        dtype=torch.float32,
    )
    advantages, returns = _advantages(transitions)

    model.train()
    metrics = {"loss": 0.0, "policy_loss": 0.0, "value_loss": 0.0, "entropy": 0.0}
    updates = 0
    count = len(transitions)

    for _ in range(PPO_EPOCHS):
        order = torch.randperm(count)
        for start in range(0, count, PPO_BATCH_SIZE):
            indexes = order[start : start + PPO_BATCH_SIZE]
            logits, values, _ = model.evaluate(
                histories[indexes],
                proprioception[indexes],
            )
            distribution = Categorical(logits=logits)
            log_probs = distribution.log_prob(actions[indexes])
            entropy = distribution.entropy().mean()
            ratio = torch.exp(log_probs - old_log_probs[indexes])
            unclipped = ratio * advantages[indexes]
            clipped = torch.clamp(
                ratio,
                1.0 - PPO_CLIP_EPS,
                1.0 + PPO_CLIP_EPS,
            ) * advantages[indexes]
            policy_loss = -torch.minimum(unclipped, clipped).mean()
            value_loss = F.mse_loss(values, returns[indexes])
            loss = (
                policy_loss
                + PPO_VALUE_COEF * value_loss
                - PPO_ENTROPY_COEF * entropy
            )

            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), PPO_MAX_GRAD_NORM)
            optimizer.step()

            metrics["loss"] += float(loss.detach())
            metrics["policy_loss"] += float(policy_loss.detach())
            metrics["value_loss"] += float(value_loss.detach())
            metrics["entropy"] += float(entropy.detach())
            updates += 1

    if updates:
        for key in metrics:
            metrics[key] /= updates
    model.eval()
    return metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train GameLab Spine + Motor")
    parser.add_argument("--episodes", type=int, default=50)
    parser.add_argument("--player", default="player1")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--target", type=float)
    args = parser.parse_args(argv)

    if args.episodes <= 0:
        raise SystemExit("--episodes must be positive")
    if args.target is not None and not 0.0 <= args.target <= WORLD_MAX_X:
        raise SystemExit("--target must be within [0,1000]")

    random.seed(args.seed)
    torch.manual_seed(args.seed)
    path = checkpoint_path()
    if args.fresh and path.exists():
        path.unlink()

    model = SpineMotorPolicy.fresh(args.seed)
    optimizer = torch.optim.Adam(model.parameters(), lr=PPO_LEARNING_RATE)
    completed = 0
    if path.exists():
        extra = load_checkpoint(path, model, optimizer=optimizer)
        completed = int(extra.get("episodes", 0))

    client = HostClient("gamelab-train")
    try:
        players = client.players()
        if args.player not in players:
            raise RuntimeError(f"unknown training player: {args.player}")

        for offset in range(1, args.episodes + 1):
            episode = completed + offset
            target = (
                float(args.target)
                if args.target is not None
                else float(random.randint(5, 995))
            )
            result = collect_episode(
                model,
                client,
                player_id=args.player,
                target_x=target,
            )
            metrics = ppo_update(model, optimizer, result.transitions)
            save_checkpoint(
                path,
                model,
                optimizer=optimizer,
                extra={"episodes": episode, "seed": args.seed},
            )
            print(
                f"Episode {episode} target={target:.1f} {result.result.upper()} "
                f"x={result.final_x:.2f} error={result.final_error:+.2f} "
                f"reward={result.reward:+.4f} steps={result.motor_steps} "
                f"requests={result.controller_requests} "
                f"loss={metrics['loss']:+.5f}",
                flush=True,
            )
        return 0
    finally:
        try:
            client.logout()
        except Exception:
            pass
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
