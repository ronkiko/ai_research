"""Joint PPO training for learned Spine CNN + one Motor MLP."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import random
import threading

import torch
from torch.distributions import Categorical
from torch.nn import functional as F

from .config import (
    PPO_BATCH_SIZE,
    PPO_CLIP_EPS,
    PPO_ENTROPY_COEF,
    PPO_EPOCHS,
    PPO_GAE_LAMBDA,
    PPO_GAMMA,
    PPO_LEARNING_RATE,
    PPO_MAX_GRAD_NORM,
    PPO_VALUE_COEF,
    SUCCESS_TOLERANCE,
    TRAIN_EPISODE_SECONDS,
    WORLD_MAX_X,
)
from .host import HostClient, player_from_state
from .unpaced import UnpacedHostClient
from .models import (
    SpineMotorPolicy,
    load_checkpoint,
    save_checkpoint,
)
from .reward import RewardConfig, RewardStore
from .runtime import checkpoint_path, ensure_player, reset_player_state


from .control import Decision as Transition, control_loop


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
    evidence: dict


def collect_episode(
    model: SpineMotorPolicy,
    client: HostClient,
    *,
    player_id: str,
    target_x: float,
    max_seconds: float = TRAIN_EPISODE_SECONDS,
    reward_config: RewardConfig | None = None,
    cancel: threading.Event | None = None,
) -> EpisodeResult:
    state = reset_player_state(client, player_id)
    transitions: list[Transition] = []
    result = control_loop(
        model, client, state, target_x=target_x, tolerance=SUCCESS_TOLERANCE,
        max_seconds=max_seconds, sampled=True, reward_config=reward_config,
        cancel=cancel, on_transition=transitions.append,
    )
    outcome = "success" if result["status"] == "reached" else result["status"]
    # Never optimize incomplete, stale, externally controlled or unconfirmed data.
    if outcome not in {"success", "timeout"}:
        transitions.clear()
    elif transitions:
        transitions[-1].done = True
    final_x = float(result.get("x", player_from_state(state)["x"]))
    return EpisodeResult(
        target_x=float(target_x), result=outcome, final_x=final_x,
        final_error=float(target_x)-final_x, reward=result["reward"],
        motor_steps=result["motor_steps"], controller_requests=result["controller_requests"],
        transitions=transitions, evidence=result,
    )


def _advantages(transitions: list[Transition]) -> tuple[torch.Tensor, torch.Tensor]:
    rewards = [item.reward for item in transitions]
    values = [item.old_value for item in transitions]
    advantages = [0.0] * len(transitions)
    gae = 0.0
    next_value = 0.0
    for index in range(len(transitions) - 1, -1, -1):
        mask = 0.0 if transitions[index].done else 1.0
        duration = transitions[index].elapsed_steps
        gamma = PPO_GAMMA ** duration
        delta = rewards[index] + gamma * next_value * mask - values[index]
        gae = delta + (PPO_GAMMA * PPO_GAE_LAMBDA) ** duration * mask * gae
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
    metrics = {key: 0.0 for key in (
        "loss", "policy_loss", "value_loss", "entropy", "approx_kl",
        "clip_fraction", "grad_norm", "value_mae",
    )}
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
            grad_norm = torch.nn.utils.clip_grad_norm_(model.parameters(), PPO_MAX_GRAD_NORM)
            optimizer.step()

            metrics["loss"] += float(loss.detach())
            metrics["policy_loss"] += float(policy_loss.detach())
            metrics["value_loss"] += float(value_loss.detach())
            metrics["entropy"] += float(entropy.detach())
            with torch.no_grad():
                metrics["approx_kl"] += float(((ratio - 1) - (log_probs - old_log_probs[indexes])).mean())
                metrics["clip_fraction"] += float(((ratio - 1).abs() > PPO_CLIP_EPS).float().mean())
                metrics["grad_norm"] += float(grad_norm)
                metrics["value_mae"] += float((values - returns[indexes]).abs().mean())
            updates += 1

    if updates:
        for key in metrics:
            metrics[key] /= updates
    model.eval()
    with torch.no_grad():
        _, predicted, _ = model.evaluate(histories, proprioception)
        variance = returns.var(unbiased=False)
        metrics["explained_variance"] = (
            float(1 - (returns - predicted).var(unbiased=False) / variance)
            if float(variance) > 1e-8 else 0.0
        )
    return metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train GameLab Spine + Motor")
    parser.add_argument("--episodes", type=int, default=50)
    parser.add_argument("--player", default="player1")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--target", type=float)
    parser.add_argument("--mode", choices=("realtime", "unpaced"), default="realtime")
    args = parser.parse_args(argv)

    if args.episodes <= 0:
        raise SystemExit("--episodes must be positive")
    if args.target is not None and not 0.0 <= args.target <= WORLD_MAX_X:
        raise SystemExit("--target must be within [0,1000]")

    random.seed(args.seed)
    torch.manual_seed(args.seed)
    path = checkpoint_path()

    model = SpineMotorPolicy.fresh(args.seed)
    optimizer = torch.optim.Adam(model.parameters(), lr=PPO_LEARNING_RATE)
    completed = 0
    if path.exists() and not args.fresh:
        extra = load_checkpoint(path, model, optimizer=optimizer)
        completed = int(extra.get("episodes", 0))

    reward_config = RewardStore().load()
    client = (
        HostClient("gamelab-train")
        if args.mode == "realtime"
        else UnpacedHostClient("gamelab-train-unpaced", player_id=args.player)
    )
    try:
        ensure_player(client, args.player)

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
                reward_config=reward_config,
            )
            if result.result not in {"success", "timeout"}:
                raise RuntimeError(f"invalid episode: {result.result}")
            metrics = ppo_update(model, optimizer, result.transitions)
            save_checkpoint(
                path,
                model,
                optimizer=optimizer,
                extra={"episodes": episode, "seed": args.seed},
            )
            print(
                f"Episode {episode} mode={args.mode} target={target:.1f} "
                f"{result.result.upper()} x={result.final_x:.2f} "
                f"error={result.final_error:+.2f} reward={result.reward:+.4f} "
                f"steps={result.motor_steps} requests={result.controller_requests} "
                f"sim={result.evidence.get('simulation_seconds', 0.0):.3f}s "
                f"wall={result.evidence.get('wall_seconds', 0.0):.3f}s "
                f"speedup={result.evidence.get('speedup', 0.0):.1f}x "
                f"loss={metrics['loss']:+.5f}",
                flush=True,
            )
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
