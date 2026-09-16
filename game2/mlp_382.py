"""The trainable game2 policy: a PyTorch MLP with shape 3-8-2."""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import os
import tempfile

import torch
from torch import nn


@dataclass(frozen=True)
class MlpDecision:
    right: bool
    jump: bool
    log_probability: torch.Tensor
    entropy: torch.Tensor
    probabilities: tuple[float, float]


class MLP382Policy:
    """Two independent Bernoulli outputs trained with episodic REINFORCE.

    Output zero controls Right and output one controls the one-tick Jump edge.
    The decision to hold or release either button remains under MLP control on
    every observation. Physics alone decides whether a jump has an effect.
    """

    ARCHITECTURE = '3-8-2'
    LEARNING_RATE = 0.01
    ENTROPY_COEF = 0.01

    def __init__(self, seed: int | None = None):
        if seed is not None:
            torch.manual_seed(seed)
        self.network = nn.Sequential(
            nn.Linear(3, 8),
            nn.ReLU(),
            nn.Linear(8, 2),
        )
        # A useful neutral prior: run is likely, jumping is uncertain. Both
        # buttons are still selected by the network and can be learned away.
        with torch.no_grad():
            self.network[-1].bias.copy_(torch.tensor([1.5, 0.0]))
        self.optimizer = torch.optim.Adam(self.network.parameters(), lr=self.LEARNING_RATE)
        self.steps = 0
        self.episodes = 0
        self.baseline = 0.0

    @staticmethod
    def _tensor(features: tuple[float, float, float]) -> torch.Tensor:
        if len(features) != 3:
            raise ValueError('3-8-2 policy expects exactly three features')
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) for value in features):
            raise ValueError('Policy features must be finite numbers')
        if not -1.0 <= features[0] <= 1.0 or features[1] not in (0.0, 1.0):
            raise ValueError('Policy features are outside their normalized ranges')
        if not -1.0 <= features[2] <= 1.0:
            raise ValueError('Policy features are outside their normalized ranges')
        return torch.tensor([features], dtype=torch.float32)

    def _logits(self, features: tuple[float, float, float]) -> torch.Tensor:
        return self.network(self._tensor(features)).reshape(2)

    def probabilities(self, features: tuple[float, float, float]) -> tuple[float, float]:
        with torch.no_grad():
            return tuple(torch.sigmoid(self._logits(features)).tolist())

    def sample(self, features: tuple[float, float, float]) -> MlpDecision:
        logits = self._logits(features)
        right_distribution = torch.distributions.Bernoulli(logits=logits[0])
        right_action = right_distribution.sample()
        log_probability = right_distribution.log_prob(right_action)
        entropy = right_distribution.entropy()
        jump_distribution = torch.distributions.Bernoulli(logits=logits[1])
        jump_action = jump_distribution.sample()
        log_probability = log_probability + jump_distribution.log_prob(jump_action)
        entropy = entropy + jump_distribution.entropy()
        probabilities = tuple(torch.sigmoid(logits).detach().tolist())
        return MlpDecision(bool(right_action.item()), bool(jump_action.item()),
                           log_probability, entropy, probabilities)

    def greedy(self, features: tuple[float, float, float]) -> MlpDecision:
        probabilities = self.probabilities(features)
        return MlpDecision(probabilities[0] >= 0.5, probabilities[1] >= 0.5,
                           torch.tensor(0.0), torch.tensor(0.0), probabilities)

    def update(self, log_probabilities: list[torch.Tensor], entropies: list[torch.Tensor],
               reward: float) -> float:
        """Apply one episodic policy-gradient update and return the loss."""
        if not log_probabilities:
            self.baseline = 0.95 * self.baseline + 0.05 * reward
            self.episodes += 1
            return 0.0
        advantage = reward - self.baseline
        log_probability = torch.stack(log_probabilities).sum()
        entropy = torch.stack(entropies).mean()
        loss = -advantage * log_probability - self.ENTROPY_COEF * entropy
        self.optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.network.parameters(), 5.0)
        self.optimizer.step()
        self.steps += len(log_probabilities)
        self.episodes += 1
        self.baseline = 0.95 * self.baseline + 0.05 * reward
        return loss.item()

    def save(self, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        checkpoint = {
            'version': 2,
            'rng_state': torch.get_rng_state(),
            'architecture': self.ARCHITECTURE,
            'network': self.network.state_dict(),
            'optimizer': self.optimizer.state_dict(),
            'steps': self.steps,
            'episodes': self.episodes,
            'baseline': self.baseline,
        }
        descriptor, temporary = tempfile.mkstemp(prefix=destination.name + '.', dir=destination.parent)
        os.close(descriptor)
        try:
            torch.save(checkpoint, temporary)
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def load(self, path: str | Path) -> None:
        checkpoint = torch.load(path, map_location='cpu', weights_only=True)
        if checkpoint.get('version') not in (1, 2):
            raise ValueError('Unsupported checkpoint version')
        if checkpoint.get('architecture') != self.ARCHITECTURE:
            raise ValueError('Checkpoint architecture is not 3-8-2')
        self.network.load_state_dict(checkpoint['network'])
        if 'optimizer' in checkpoint:
            self.optimizer.load_state_dict(checkpoint['optimizer'])
        self.steps = int(checkpoint.get('steps', 0))
        self.episodes = int(checkpoint.get('episodes', 0))
        self.baseline = float(checkpoint.get('baseline', 0.0))
        if 'rng_state' in checkpoint:
            torch.set_rng_state(checkpoint['rng_state'])

    def stats(self) -> dict:
        return {
            'architecture': self.ARCHITECTURE,
            'parameters': sum(parameter.numel() for parameter in self.network.parameters()),
            'steps': self.steps,
            'episodes': self.episodes,
            'baseline': round(self.baseline, 4),
        }
