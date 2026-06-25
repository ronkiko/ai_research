from __future__ import annotations

from collections import deque

from modules.base import AiHost, MechanicsHost, Observation

from .dto import RunStateDto


class RunSession:
    WINDOW = 1000

    def __init__(self, host: MechanicsHost | None, ai: AiHost | None, speed: int = 0):
        self._host = host
        self._ai = ai
        self._speed = speed
        self._reward = 0
        self._steps = 0
        self._running = False
        self._hits: deque[int] = deque()

    def set_speed(self, speed: int) -> None:
        self._speed = max(0, int(speed))

    def start(self) -> RunStateDto:
        if self._ai is not None:
            model = self._ai.active_model_info()
            if model is not None:
                self._ai.reset_model(model.key)
        self._reward = 0
        self._steps = 0
        self._hits.clear()
        self._running = True
        return self.state()

    def stop(self) -> RunStateDto:
        self._running = False
        return self.state()

    def tick(self) -> RunStateDto:
        if self._host is None or self._ai is None:
            return self.state()

        obs = self._host.active_observe()
        if obs is None:
            return self.state()

        action = self._ai.act(obs)
        if action is None:
            return self.state()

        outcome = self._host.active_step(action)
        if outcome is None:
            return self.state()

        self._ai.train(obs, outcome)
        self._reward += outcome.reward
        self._hits.append(1 if outcome.reward > 0 else 0)
        if len(self._hits) > self.WINDOW:
            self._hits.popleft()
        self._steps += 1
        return self.state()

    def run_steps(self, n: int) -> RunStateDto:
        count = max(1, min(int(n), 100000))
        for _ in range(count):
            self.tick()
        return self.state()

    def state(self) -> RunStateDto:
        game = self._host.active_mechanics() if self._host is not None else None
        model = self._ai.active_model_info() if self._ai is not None else None
        mode = self._ai.active_train_mode() if self._ai is not None else None
        stats = self._ai.model_stats(model.key) if self._ai is not None and model is not None else None
        return RunStateDto(
            running=self._running,
            reward=self._reward,
            steps=self._steps,
            accuracy=self.accuracy(),
            speed=self._speed,
            active_game=game.key if game is not None else "",
            active_model=model.key if model is not None else "",
            active_mode=mode or "",
            logit=stats.logit if stats is not None else 0.0,
            prob=stats.prob if stats is not None else 0.5,
        )

    def accuracy(self) -> int:
        if not self._hits:
            return 0
        return round(100 * sum(self._hits) / len(self._hits))
