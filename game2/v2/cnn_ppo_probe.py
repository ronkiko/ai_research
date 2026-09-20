"""Standalone CNN+PPO sanity probe.

This module intentionally imports no Game2 runtime, Engine, VisionGrid, maps,
checkpoints, or realtime code.  It answers one question only: can a tiny CNN
and PPO learn a trivial visual left/right classification task quickly on this
machine?
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time

import torch
from torch import nn
from torch.distributions import Categorical


IMAGE_SIZE = 28
SQUARE_SIZE = 4
LEFT_X = 4
RIGHT_X = 20
SQUARE_Y = 12


class ProbeModel(nn.Module):
    """Tiny shared CNN backbone with actor and critic heads."""

    def __init__(self) -> None:
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(1, 8, kernel_size=3, stride=2, padding=1),
            nn.ReLU(),
            nn.Conv2d(8, 16, kernel_size=3, stride=2, padding=1),
            nn.ReLU(),
            nn.Flatten(),
            nn.Linear(16 * 7 * 7, 32),
            nn.ReLU(),
        )
        self.actor = nn.Linear(32, 2)
        self.critic = nn.Linear(32, 1)

    def forward(self, observation: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        hidden = self.features(observation)
        return self.actor(hidden), self.critic(hidden).squeeze(-1)


def make_batch(
    count: int,
    *,
    generator: torch.Generator,
    device: torch.device,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Create independent one-step visual episodes.

    label 0 -> white square on the left
    label 1 -> white square on the right
    """
    if type(count) is not int or count <= 0:
        raise ValueError("count must be a positive integer")
    labels = torch.randint(0, 2, (count,), generator=generator)
    images = torch.zeros((count, 1, IMAGE_SIZE, IMAGE_SIZE), dtype=torch.float32)
    images[
        labels == 0,
        0,
        SQUARE_Y:SQUARE_Y + SQUARE_SIZE,
        LEFT_X:LEFT_X + SQUARE_SIZE,
    ] = 1.0
    images[
        labels == 1,
        0,
        SQUARE_Y:SQUARE_Y + SQUARE_SIZE,
        RIGHT_X:RIGHT_X + SQUARE_SIZE,
    ] = 1.0
    return images.to(device), labels.to(device)


def parameter_hash(model: nn.Module) -> str:
    digest = hashlib.sha256()
    for parameter in model.parameters():
        tensor = parameter.detach().cpu().contiguous()
        digest.update(tensor.numpy().tobytes())
    return digest.hexdigest()


@torch.no_grad()
def evaluate(
    model: ProbeModel,
    *,
    count: int,
    generator: torch.Generator,
    device: torch.device,
) -> float:
    observations, labels = make_batch(
        count, generator=generator, device=device
    )
    logits, _values = model(observations)
    predictions = logits.argmax(dim=1)
    return float((predictions == labels).float().mean())


def collect_rollout(
    model: ProbeModel,
    *,
    count: int,
    generator: torch.Generator,
    device: torch.device,
) -> dict[str, torch.Tensor | float]:
    observations, labels = make_batch(
        count, generator=generator, device=device
    )
    with torch.no_grad():
        logits, values = model(observations)
        distribution = Categorical(logits=logits)
        actions = distribution.sample()
        old_log_prob = distribution.log_prob(actions)
        rewards = torch.where(
            actions == labels,
            torch.ones_like(values),
            -torch.ones_like(values),
        )
    return {
        "observations": observations,
        "actions": actions,
        "old_log_prob": old_log_prob,
        "returns": rewards,
        "advantages": rewards - values,
        "mean_reward": float(rewards.mean()),
    }


def ppo_update(
    model: ProbeModel,
    optimizer: torch.optim.Optimizer,
    rollout: dict[str, torch.Tensor | float],
    *,
    clip_epsilon: float,
    entropy_coefficient: float,
    value_coefficient: float,
    epochs: int,
    minibatch_size: int,
    generator: torch.Generator,
) -> float:
    observations = rollout["observations"]
    actions = rollout["actions"]
    old_log_prob = rollout["old_log_prob"]
    returns = rollout["returns"]
    advantages = rollout["advantages"]
    assert isinstance(observations, torch.Tensor)
    assert isinstance(actions, torch.Tensor)
    assert isinstance(old_log_prob, torch.Tensor)
    assert isinstance(returns, torch.Tensor)
    assert isinstance(advantages, torch.Tensor)

    advantages = advantages.detach()
    if len(advantages) > 1:
        advantages = (
            advantages - advantages.mean()
        ) / (advantages.std(unbiased=False) + 1e-8)

    total_loss = 0.0
    optimizer_steps = 0
    count = observations.shape[0]

    for _epoch in range(epochs):
        order = torch.randperm(count, generator=generator, device="cpu").to(
            observations.device
        )
        for start in range(0, count, minibatch_size):
            indexes = order[start:start + minibatch_size]
            logits, values = model(observations[indexes])
            distribution = Categorical(logits=logits)
            new_log_prob = distribution.log_prob(actions[indexes])
            entropy = distribution.entropy().mean()

            ratio = torch.exp(new_log_prob - old_log_prob[indexes])
            batch_advantages = advantages[indexes]
            unclipped = ratio * batch_advantages
            clipped = torch.clamp(
                ratio, 1.0 - clip_epsilon, 1.0 + clip_epsilon
            ) * batch_advantages
            policy_loss = -torch.minimum(unclipped, clipped).mean()
            value_loss = nn.functional.mse_loss(values, returns[indexes])
            loss = (
                policy_loss
                + value_coefficient * value_loss
                - entropy_coefficient * entropy
            )

            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            optimizer.step()

            total_loss += float(loss.detach())
            optimizer_steps += 1

    return total_loss / max(optimizer_steps, 1)


def _bar(value: float, width: int = 20) -> str:
    value = max(0.0, min(1.0, float(value)))
    filled = min(width, int(round(value * width)))
    return "█" * filled + "-" * (width - filled)


def run_probe(
    *,
    seed: int = 1,
    updates: int = 20,
    rollout_size: int = 256,
    minibatch_size: int = 64,
    epochs: int = 4,
    evaluation_size: int = 1024,
    target_accuracy: float = 0.98,
    learning_rate: float = 1e-3,
    clip_epsilon: float = 0.2,
    entropy_coefficient: float = 0.01,
    value_coefficient: float = 0.5,
    device_name: str = "cpu",
    threads: int = 1,
    json_output: bool = False,
) -> int:
    if updates <= 0 or rollout_size <= 0 or minibatch_size <= 0:
        raise ValueError("updates and batch sizes must be positive")
    if epochs <= 0 or evaluation_size <= 0 or threads <= 0:
        raise ValueError("epochs, evaluation_size and threads must be positive")
    if not 0.5 < target_accuracy <= 1.0:
        raise ValueError("target_accuracy must be in (0.5, 1.0]")
    if device_name == "auto":
        device_name = "cuda" if torch.cuda.is_available() else "cpu"
    if device_name == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but not available")
    device = torch.device(device_name)

    torch.set_num_threads(threads)
    torch.manual_seed(seed + 4000)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed + 4000)
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(seed)
        model = ProbeModel().to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)

    rollout_generator = torch.Generator(device="cpu")
    rollout_generator.manual_seed(seed + 1000)
    optimization_generator = torch.Generator(device="cpu")
    optimization_generator.manual_seed(seed + 2000)
    evaluation_generator = torch.Generator(device="cpu")
    evaluation_generator.manual_seed(seed + 3000)

    before_hash = parameter_hash(model)
    started = time.perf_counter()
    initial_accuracy = evaluate(
        model,
        count=evaluation_size,
        generator=evaluation_generator,
        device=device,
    )

    def emit(kind: str, payload: dict[str, object]) -> None:
        if json_output:
            print(kind + " " + json.dumps(
                payload, separators=(",", ":"), sort_keys=True
            ), flush=True)
            return
        if kind == "START":
            print(
                f"CNN+PPO probe · {device.type} · threads={threads} · "
                f"initial accuracy {100.0 * initial_accuracy:.1f}%",
                flush=True,
            )
        elif kind == "UPDATE":
            print(
                f"Update {int(payload['update']):>2}/{updates} "
                f"[{_bar(float(payload['accuracy']))}] "
                f"accuracy {100.0 * float(payload['accuracy']):5.1f}% · "
                f"reward {float(payload['reward']):+5.2f} · "
                f"loss {float(payload['loss']):.4f}",
                flush=True,
            )
        elif kind == "RESULT":
            status = "PASS" if payload["passed"] else "FAIL"
            print(
                f"{status} · accuracy {100.0 * float(payload['accuracy']):.1f}% · "
                f"{float(payload['seconds']):.3f}s · "
                f"{int(payload['samples'])} samples · "
                f"weights changed {'yes' if payload['weights_changed'] else 'no'}",
                flush=True,
            )

    emit("START", {
        "device": device.type,
        "threads": threads,
        "seed": seed,
        "initial_accuracy": initial_accuracy,
    })

    final_accuracy = initial_accuracy
    samples = 0
    used_updates = 0
    passed = final_accuracy >= target_accuracy

    for update in range(1, updates + 1):
        if passed:
            break
        rollout = collect_rollout(
            model,
            count=rollout_size,
            generator=rollout_generator,
            device=device,
        )
        loss = ppo_update(
            model,
            optimizer,
            rollout,
            clip_epsilon=clip_epsilon,
            entropy_coefficient=entropy_coefficient,
            value_coefficient=value_coefficient,
            epochs=epochs,
            minibatch_size=minibatch_size,
            generator=optimization_generator,
        )
        samples += rollout_size
        used_updates = update
        final_accuracy = evaluate(
            model,
            count=evaluation_size,
            generator=evaluation_generator,
            device=device,
        )
        mean_reward = float(rollout["mean_reward"])
        emit("UPDATE", {
            "update": update,
            "updates": updates,
            "accuracy": final_accuracy,
            "reward": mean_reward,
            "loss": loss,
        })
        passed = final_accuracy >= target_accuracy

    elapsed = time.perf_counter() - started
    after_hash = parameter_hash(model)
    emit("RESULT", {
        "passed": passed,
        "accuracy": final_accuracy,
        "initial_accuracy": initial_accuracy,
        "seconds": elapsed,
        "samples": samples,
        "updates": used_updates,
        "weights_changed": before_hash != after_hash,
    })
    return 0 if passed else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Standalone synthetic CNN+PPO learning sanity probe"
    )
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--updates", type=int, default=20)
    parser.add_argument("--rollout-size", type=int, default=256)
    parser.add_argument("--minibatch-size", type=int, default=64)
    parser.add_argument("--epochs", type=int, default=4)
    parser.add_argument("--evaluation-size", type=int, default=1024)
    parser.add_argument("--target-accuracy", type=float, default=0.98)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--clip-epsilon", type=float, default=0.2)
    parser.add_argument("--entropy-coefficient", type=float, default=0.01)
    parser.add_argument("--value-coefficient", type=float, default=0.5)
    parser.add_argument("--device", choices=("cpu", "cuda", "auto"), default="cpu")
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    return run_probe(
        seed=args.seed,
        updates=args.updates,
        rollout_size=args.rollout_size,
        minibatch_size=args.minibatch_size,
        epochs=args.epochs,
        evaluation_size=args.evaluation_size,
        target_accuracy=args.target_accuracy,
        learning_rate=args.learning_rate,
        clip_epsilon=args.clip_epsilon,
        entropy_coefficient=args.entropy_coefficient,
        value_coefficient=args.value_coefficient,
        device_name=args.device,
        threads=args.threads,
        json_output=args.json,
    )


if __name__ == "__main__":
    raise SystemExit(main())
