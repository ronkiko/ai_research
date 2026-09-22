"""Long-lived MCP-facing GameLab service."""
from __future__ import annotations

from collections import deque
import random
import threading
from typing import Any

import torch

from .config import (
    DEFAULT_GOAL_TIMEOUT,
    PPO_LEARNING_RATE,
    SUCCESS_TOLERANCE,
    TRAIN_EPISODE_SECONDS,
    WORLD_MAX_X,
    WORLD_MIN_X,
)
from .host import HostClient, HostError
from .models import SpineMotorPolicy, load_checkpoint, save_checkpoint
from .reward import RewardConfig, RewardStore
from .runtime import GoalRunner, checkpoint_path, ensure_player, reset_player_state
from .training import collect_episode, ppo_update


class LaboratoryBusyError(RuntimeError):
    pass


class Laboratory:
    """Owns asynchronous training, verification, and model-run operations."""

    def __init__(self, *, player_id: str = "player1") -> None:
        self.player_id = player_id
        self.reward_store = RewardStore()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._active_kind: str | None = None
        self._cancel = threading.Event()
        self._records: dict[str, dict[str, Any]] = {
            "training": {"status": "idle"},
            "verify": {"status": "idle"},
            "run": {"status": "idle"},
        }
        self.ensure_model()

    def ensure_model(self) -> None:
        path = checkpoint_path()
        if path.is_file():
            return
        model = SpineMotorPolicy.fresh(1)
        save_checkpoint(path, model, extra={"episodes": 0, "seed": 1})

    def model_info(self) -> dict[str, Any]:
        self.ensure_model()
        model = SpineMotorPolicy()
        extra = load_checkpoint(checkpoint_path(), model)
        return {
            "checkpoint_ready": True,
            "checkpoint": checkpoint_path().name,
            "trainable": True,
            "goal_interface": "target_x",
            "parameters": sum(parameter.numel() for parameter in model.parameters()),
            "episodes_trained": int(extra.get("episodes", 0)),
        }

    def _require_attached_player(self) -> None:
        client = HostClient("gamelab-preflight")
        try:
            ensure_player(client, self.player_id)
        finally:
            client.close()

    def reward_get(self) -> dict[str, float]:
        return self.reward_store.load().public()

    def reward_set(self, **changes: float | None) -> dict[str, float]:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise LaboratoryBusyError(
                    "cannot change reward configuration while an experiment is active"
                )
            updated = self.reward_store.load().updated(**changes)
            return self.reward_store.save(updated).public()

    def active_operation(self) -> str | None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self._active_kind
            return None

    def _start(
        self,
        kind: str,
        initial: dict[str, Any],
        worker,
        *args,
    ) -> dict[str, Any]:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                active_status = (
                    self._records.get(self._active_kind or "", {})
                    .get("status")
                )
                terminal = {
                    "completed",
                    "cancelled",
                    "failed",
                    "passed",
                    "reached",
                    "timeout",
                }
                if active_status not in terminal:
                    raise LaboratoryBusyError(
                        f"laboratory is busy with {self._active_kind}"
                    )
            self._cancel = threading.Event()
            self._active_kind = kind
            record = {"status": "starting", **initial}
            self._records[kind] = record
            thread = threading.Thread(
                target=self._guarded_worker,
                args=(kind, worker, args),
                daemon=True,
                name=f"gamelab-{kind}",
            )
            self._thread = thread
            thread.start()
            return dict(record)

    def _guarded_worker(self, kind: str, worker, args: tuple[Any, ...]) -> None:
        try:
            worker(*args)
        except Exception as exc:
            with self._lock:
                current = dict(self._records[kind])
                current.update(status="failed", error=str(exc))
                self._records[kind] = current
        finally:
            current = threading.current_thread()
            with self._lock:
                if self._thread is current:
                    self._active_kind = None
                    self._thread = None

    def status(self, kind: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._records[kind])

    def cancel(self, kind: str) -> dict[str, Any]:
        with self._lock:
            active = (
                self._thread is not None
                and self._thread.is_alive()
                and self._active_kind == kind
            )
            if active:
                self._cancel.set()
                current = dict(self._records[kind])
                current["cancel_requested"] = True
                self._records[kind] = current
            return {
                "accepted": bool(active),
                "operation": kind,
                "status": self._records[kind].get("status"),
            }

    @staticmethod
    def _target(value: float | None) -> float | None:
        if value is None:
            return None
        value = float(value)
        if not WORLD_MIN_X <= value <= WORLD_MAX_X:
            raise ValueError("target_x must be within [0,1000]")
        return value

    @staticmethod
    def _seconds(value: float, *, name: str) -> float:
        value = float(value)
        if not 0.25 <= value <= 120.0:
            raise ValueError(f"{name} must be within [0.25,120]")
        return value

    def start_training(
        self,
        *,
        episodes: int,
        target_x: float | None,
        fresh: bool,
        seed: int,
        max_seconds: float,
    ) -> dict[str, Any]:
        if type(episodes) is not int or not 1 <= episodes <= 500:
            raise ValueError("episodes must be within [1,500]")
        if type(seed) is not int:
            raise ValueError("seed must be an integer")
        target = self._target(target_x)
        seconds = self._seconds(max_seconds, name="max_seconds")
        reward = self.reward_store.load()
        self._require_attached_player()
        return self._start(
            "training",
            {
                "episodes_requested": episodes,
                "episodes_completed": 0,
                "target_x": target,
                "fresh": bool(fresh),
                "seed": seed,
                "max_seconds": seconds,
                "reward": reward.public(),
                "recent_episodes": [],
            },
            self._training_worker,
            episodes,
            target,
            bool(fresh),
            seed,
            seconds,
            reward,
        )

    def _training_worker(
        self,
        episodes: int,
        target_x: float | None,
        fresh: bool,
        seed: int,
        max_seconds: float,
        reward: RewardConfig,
    ) -> None:
        random.seed(seed)
        torch.manual_seed(seed)
        path = checkpoint_path()
        if fresh and path.exists():
            path.unlink()

        model = SpineMotorPolicy.fresh(seed)
        optimizer = torch.optim.Adam(model.parameters(), lr=PPO_LEARNING_RATE)
        prior = 0
        if fresh:
            save_checkpoint(
                path,
                model,
                optimizer=optimizer,
                extra={"episodes": 0, "seed": seed},
            )
        elif path.exists():
            extra = load_checkpoint(path, model, optimizer=optimizer)
            prior = int(extra.get("episodes", 0))
        else:
            save_checkpoint(
                path,
                model,
                optimizer=optimizer,
                extra={"episodes": 0, "seed": seed},
            )

        client = HostClient("gamelab-mcp-train")
        recent: deque[dict[str, Any]] = deque(maxlen=20)
        try:
            ensure_player(client, self.player_id)

            completed = 0
            for offset in range(1, episodes + 1):
                if self._cancel.is_set():
                    break
                episode_number = prior + offset
                target = (
                    float(target_x)
                    if target_x is not None
                    else float(random.randint(5, 995))
                )
                result = collect_episode(
                    model,
                    client,
                    player_id=self.player_id,
                    target_x=target,
                    max_seconds=max_seconds,
                    reward_config=reward,
                    cancel=self._cancel,
                )
                if result.result == "cancelled":
                    break

                metrics = ppo_update(model, optimizer, result.transitions)
                completed += 1
                save_checkpoint(
                    path,
                    model,
                    optimizer=optimizer,
                    extra={"episodes": prior + completed, "seed": seed},
                )
                summary = {
                    "episode": episode_number,
                    "target_x": target,
                    "result": result.result,
                    "final_x": result.final_x,
                    "final_error": result.final_error,
                    "reward": result.reward,
                    "motor_steps": result.motor_steps,
                    "controller_requests": result.controller_requests,
                    "loss": metrics["loss"],
                    "policy_loss": metrics["policy_loss"],
                    "value_loss": metrics["value_loss"],
                    "entropy": metrics["entropy"],
                }
                recent.append(summary)
                with self._lock:
                    record = dict(self._records["training"])
                    record.update(
                        status="running",
                        episodes_completed=completed,
                        last_episode=summary,
                        recent_episodes=list(recent),
                    )
                    self._records["training"] = record

            with self._lock:
                record = dict(self._records["training"])
                record.update(
                    status="cancelled" if self._cancel.is_set() else "completed",
                    episodes_completed=completed,
                    recent_episodes=list(recent),
                    checkpoint_ready=path.is_file(),
                )
                self._records["training"] = record
        finally:
            client.close()

    def start_verify(
        self,
        *,
        target_x: float,
        runs: int,
        tolerance: float,
        max_seconds: float,
    ) -> dict[str, Any]:
        target = self._target(target_x)
        assert target is not None
        if type(runs) is not int or not 1 <= runs <= 20:
            raise ValueError("runs must be within [1,20]")
        tolerance = float(tolerance)
        if not 0.0 < tolerance <= 25.0:
            raise ValueError("tolerance must be within (0,25]")
        seconds = self._seconds(max_seconds, name="max_seconds")
        self.ensure_model()
        self._require_attached_player()
        return self._start(
            "verify",
            {
                "target_x": target,
                "runs_requested": runs,
                "runs_completed": 0,
                "passes": 0,
                "tolerance": tolerance,
                "max_seconds": seconds,
                "results": [],
            },
            self._verify_worker,
            target,
            runs,
            tolerance,
            seconds,
        )

    def _verify_worker(
        self,
        target_x: float,
        runs: int,
        tolerance: float,
        max_seconds: float,
    ) -> None:
        model = SpineMotorPolicy()
        load_checkpoint(checkpoint_path(), model)
        model.eval()
        client = HostClient("gamelab-mcp-verify")
        results: list[dict[str, Any]] = []
        passed = 0
        try:
            ensure_player(client, self.player_id)
            runner = GoalRunner(model, client, player_id=self.player_id)
            for index in range(1, runs + 1):
                if self._cancel.is_set():
                    break
                reset_player_state(client, self.player_id)
                result = runner.run(
                    target_x,
                    tolerance=tolerance,
                    max_seconds=max_seconds,
                    cancel=self._cancel,
                )
                ok = result.get("status") == "reached"
                passed += int(ok)
                item = {"run": index, "pass": ok, **result}
                results.append(item)
                with self._lock:
                    record = dict(self._records["verify"])
                    record.update(
                        status="running",
                        runs_completed=len(results),
                        passes=passed,
                        results=list(results),
                    )
                    self._records["verify"] = record
                if self._cancel.is_set():
                    break

            with self._lock:
                record = dict(self._records["verify"])
                record.update(
                    status=(
                        "cancelled"
                        if self._cancel.is_set()
                        else ("passed" if passed == runs else "failed")
                    ),
                    runs_completed=len(results),
                    passes=passed,
                    results=list(results),
                )
                self._records["verify"] = record
        finally:
            client.close()

    def start_run(
        self,
        *,
        target_x: float,
        tolerance: float,
        max_seconds: float,
    ) -> dict[str, Any]:
        target = self._target(target_x)
        assert target is not None
        tolerance = float(tolerance)
        if not 0.0 < tolerance <= 25.0:
            raise ValueError("tolerance must be within (0,25]")
        seconds = self._seconds(max_seconds, name="max_seconds")
        self.ensure_model()
        self._require_attached_player()
        return self._start(
            "run",
            {
                "target_x": target,
                "tolerance": tolerance,
                "max_seconds": seconds,
            },
            self._run_worker,
            target,
            tolerance,
            seconds,
        )

    def _run_worker(
        self,
        target_x: float,
        tolerance: float,
        max_seconds: float,
    ) -> None:
        model = SpineMotorPolicy()
        load_checkpoint(checkpoint_path(), model)
        model.eval()
        client = HostClient("gamelab-mcp-run")
        try:
            runner = GoalRunner(model, client, player_id=self.player_id)

            def update(status: dict[str, Any]) -> None:
                with self._lock:
                    record = dict(self._records["run"])
                    record.update(status)
                    self._records["run"] = record

            result = runner.run(
                target_x,
                tolerance=tolerance,
                max_seconds=max_seconds,
                cancel=self._cancel,
                on_status=update,
            )
            with self._lock:
                self._records["run"] = dict(result)
        finally:
            client.close()


__all__ = ["Laboratory", "LaboratoryBusyError"]
