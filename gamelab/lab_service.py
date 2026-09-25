"""Long-lived MCP-facing GameLab service."""
from __future__ import annotations

from collections import deque
import threading
from typing import Any

import torch

from organism.config import (
    DEFAULT_GOAL_TIMEOUT,
    SUCCESS_TOLERANCE,
    TRAIN_EPISODE_SECONDS,
    WORLD_MAX_X,
    WORLD_MIN_X,
)
from organism.host import HostClient, HostError
from organism.models import (
    build_spine_policy,
    load_checkpoint,
    model_for_checkpoint,
    policy_id,
)
from organism.motors.package import DEFAULT_MOTOR_ID, MotorPackageError, list_motor_packages
from organism.control import GoalMailbox
from organism.journal import Journal
from organism.reward import RewardConfig, RewardStore
from organism.runtime import GoalRunner, checkpoint_path, ensure_player, reset_player_state
from organism.lease import BodyLease, BodyLeaseBusy, BodyLeaseHandle
from organism.training import _prepare_reward_config
from organism.spine_school import ALGORITHM, train_school


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
        self._goals: GoalMailbox | None = None
        self._journal: Journal | None = None
        self._body_lease = BodyLease()
        self._body_lease_handle: BodyLeaseHandle | None = None
        self._records: dict[str, dict[str, Any]] = {
            "training": {"status": "idle"},
            "verify": {"status": "idle"},
            "run": {"status": "idle"},
        }
        self._model_error: str | None = None
        self.ensure_model()

    def ensure_model(self) -> bool:
        path = checkpoint_path()
        if not path.is_file():
            self._model_error = None
            return False
        try:
            model_for_checkpoint(path)
        except Exception as exc:
            # A stale/corrupt learned artifact must never kill the MCP service.
            # Treat it as "no usable Spine" and expose the reason through
            # model_info so the Brain can start fresh training.
            self._model_error = f"{type(exc).__name__}: {exc}"
            return False
        self._model_error = None
        return True

    def _fresh_spine_preflight(self) -> tuple[str | None, str | None]:
        try:
            _, package = build_spine_policy(DEFAULT_MOTOR_ID, seed=1)
        except Exception as exc:
            return None, f"{type(exc).__name__}: {exc}"
        return package.motor_id, None

    def model_info(self) -> dict[str, Any]:
        path = checkpoint_path()
        selected_motor_id, training_preflight_error = self._fresh_spine_preflight()
        trainable = selected_motor_id is not None
        if not self.ensure_model():
            motors = list_motor_packages()
            return {
                "checkpoint_ready": False,
                "checkpoint": path.name,
                "checkpoint_error": self._model_error,
                "trainable": trainable,
                "training_motor_selector": DEFAULT_MOTOR_ID,
                "selected_motor_id": selected_motor_id,
                "training_preflight_error": training_preflight_error,
                "goal_interface": "target_x",
                "motor_interface": "built_motor_instance",
                "episodes_trained": 0,
                "motors": motors,
            }
        model, package = model_for_checkpoint(path)
        extra = load_checkpoint(path, model)
        return {
            "checkpoint_ready": True,
            "checkpoint": path.name,
            "trainable": trainable,
            "training_motor_selector": DEFAULT_MOTOR_ID,
            "selected_motor_id": selected_motor_id,
            "training_preflight_error": training_preflight_error,
            "goal_interface": "target_x",
            "motor_interface": "built_motor_instance",
            "motor_id": package.motor_id,
            "parameters": sum(parameter.numel() for parameter in model.parameters()),
            "episodes_trained": int(extra.get("episodes", 0)),
            "algorithm": extra.get("algorithm", "legacy_ppo"),
            "spine_verification": extra.get("spine_verification"),
            "policy_id": policy_id(model),
            "motors": list_motor_packages(),
        }

    def _require_attached_player(self, host_id: str) -> str:
        client = HostClient("gamelab-preflight", host_id=host_id)
        try:
            session = client.session()
            player_id = session.get("player_id")
            if not isinstance(player_id, str) or not player_id:
                raise HostError("selected Host has no active player")
            ensure_player(client, player_id)
            return player_id
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
                raise LaboratoryBusyError(f"laboratory is busy with {self._active_kind}")
            try:
                lease_handle = self._body_lease.acquire(
                    f"gamelab:{kind}",
                    kind,
                )
            except BodyLeaseBusy as exc:
                raise LaboratoryBusyError(str(exc)) from exc
            try:
                self._cancel = threading.Event()
                self._active_kind = kind
                record = {"status": "starting", **initial}
                self._journal = Journal(checkpoint_path(), kind, initial)
                record["experiment_id"] = self._journal.experiment_id
                self._goals = GoalMailbox(initial["target_x"]) if kind == "run" else None
                self._records[kind] = record
                thread = threading.Thread(
                    target=self._guarded_worker,
                    args=(kind, worker, args),
                    daemon=True,
                    name=f"gamelab-{kind}",
                )
                self._thread = thread
                self._body_lease_handle = lease_handle
                thread.start()
                return dict(record)
            except Exception:
                lease_handle.release()
                self._body_lease_handle = None
                self._active_kind = None
                self._thread = None
                raise

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
                if self._journal is not None:
                    try:
                        self._journal.append("finished", self._records[kind])
                    except OSError as exc:
                        self._records[kind].update(status="failed", error=f"evidence write failed: {exc}")
                if self._thread is current:
                    self._active_kind = None
                    self._thread = None
                lease_handle = self._body_lease_handle
                self._body_lease_handle = None
            if lease_handle is not None:
                lease_handle.release()

    def update_goal(self, target_x: float) -> dict[str, Any]:
        target = self._target(target_x)
        if target is None:
            raise ValueError("target_x is required")
        with self._lock:
            if self.active_operation() != "run" or self._goals is None:
                raise LaboratoryBusyError("no active live run")
            if self._records["run"].get("status") not in {"starting", "active"}:
                raise LaboratoryBusyError("live run is finishing")
            revision = self._goals.update(target)
            response = dict(accepted=True, target_x=target, requested_goal_revision=revision,
                            experiment_id=self._records["run"]["experiment_id"])
            if self._journal is not None:
                self._journal.append("goal_update", response)
            return response

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
        host_id: str,
        motor_id: str = DEFAULT_MOTOR_ID,
    ) -> dict[str, Any]:
        if type(episodes) is not int or not 1 <= episodes <= 500:
            raise ValueError("episodes must be within [1,500]")
        if type(seed) is not int:
            raise ValueError("seed must be an integer")
        target = self._target(target_x)
        seconds = self._seconds(max_seconds, name="max_seconds")
        player_id = self._require_attached_player(host_id)
        try:
            _, motor_package = build_spine_policy(motor_id, seed=seed)
        except MotorPackageError as exc:
            raise ValueError(str(exc)) from exc
        reward = _prepare_reward_config(self.reward_store, fresh=bool(fresh))
        return self._start(
            "training",
            {
                "episodes_requested": episodes,
                "algorithm": ALGORITHM,
                "mode": "realtime",
                "episodes_completed": 0,
                "target_x": target,
                "fresh": bool(fresh),
                "seed": seed,
                "max_seconds": seconds,
                "reward": reward.public(),
                "recent_episodes": [],
                "host_id": host_id,
                "player_id": player_id,
                "motor_id": motor_package.motor_id,
                "motor_brain_sha256": motor_package.brain_sha256,
            },
            self._training_worker,
            episodes,
            target,
            bool(fresh),
            seed,
            seconds,
            reward,
            host_id,
            player_id,
            motor_package.motor_id,
        )

    def _training_worker(
        self,
        episodes: int,
        target_x: float | None,
        fresh: bool,
        seed: int,
        max_seconds: float,
        reward: RewardConfig,
        host_id: str,
        player_id: str,
        motor_id: str,
    ) -> None:
        client = HostClient("gamelab-mcp-train", host_id=host_id)
        prior_threads = torch.get_num_threads()
        torch.set_num_threads(1)
        recent: deque[dict[str, Any]] = deque(maxlen=20)
        completed = 0

        def report(summary):
            nonlocal completed
            completed += 1
            if self._journal is not None:
                self._journal.append("update", summary)
            recent.append(summary)
            with self._lock:
                record = dict(self._records["training"])
                record.update(status="running", episodes_completed=completed,
                              algorithm=ALGORITHM, last_episode=summary,
                              recent_episodes=list(recent))
                self._records["training"] = record

        def record_rollout(result, identity, episode):
            if self._journal is not None:
                self._journal.append("rollout", {
                    "episode": episode, "policy_id": identity,
                    "target_x": result.target_x, "evidence": result.evidence,
                    "motor_transitions": result.motor_transitions,
                    "transitions": [{
                        "tick": t.tick, "next_tick": t.next_tick,
                        "elapsed_steps": t.elapsed_steps, "action": t.action,
                        "history": t.history.tolist(),
                        "proprioception": t.proprioception.tolist(),
                        "sequence": t.sequence, "command_id": t.command_id,
                        "applied_tick": t.applied_tick, "reward": t.reward,
                        "input_delay": t.input_delay,
                    } for t in result.transitions],
                })

        try:
            result = train_school(
                client, motor_id=motor_id, episodes=episodes, seed=seed,
                fresh=fresh, player_id=player_id, target_x=target_x,
                max_seconds=max_seconds, cancel=self._cancel,
                on_episode=report, on_rollout=record_rollout, final_verify=False,
            )
            with self._lock:
                record = dict(self._records["training"])
                record.update(result)
                record.update(status="cancelled" if result["cancelled"] else "completed",
                              recent_episodes=list(recent))
                self._records["training"] = record
        finally:
            client.close()
            torch.set_num_threads(prior_threads)

    def start_verify(
        self,
        *,
        target_x: float,
        runs: int,
        tolerance: float,
        max_seconds: float,
        host_id: str,
    ) -> dict[str, Any]:
        target = self._target(target_x)
        assert target is not None
        if type(runs) is not int or not 1 <= runs <= 20:
            raise ValueError("runs must be within [1,20]")
        tolerance = float(tolerance)
        if not 0.0 < tolerance <= 25.0:
            raise ValueError("tolerance must be within (0,25]")
        seconds = self._seconds(max_seconds, name="max_seconds")
        if not self.ensure_model():
            raise ValueError("no compatible Spine checkpoint; train with a verified Motor first")
        player_id = self._require_attached_player(host_id)
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
                "host_id": host_id,
                "player_id": player_id,
            },
            self._verify_worker,
            target,
            runs,
            tolerance,
            seconds,
            host_id,
            player_id,
        )

    def _verify_worker(
        self,
        target_x: float,
        runs: int,
        tolerance: float,
        max_seconds: float,
        host_id: str,
        player_id: str,
    ) -> None:
        model, _ = model_for_checkpoint(checkpoint_path())
        with self._lock:
            self._records["verify"]["policy_id"] = policy_id(model)
        client = HostClient("gamelab-mcp-verify", host_id=host_id)
        results: list[dict[str, Any]] = []
        passed = 0
        try:
            ensure_player(client, player_id)
            runner = GoalRunner(model, client, player_id=player_id)
            for index in range(1, runs + 1):
                if self._cancel.is_set():
                    break
                reset_player_state(client, player_id)
                result = runner.run(
                    target_x,
                    tolerance=tolerance,
                    max_seconds=max_seconds,
                    cancel=self._cancel,
                )
                ok = result.get("status") == "reached" and result.get("wall_contacts") == 0
                passed += int(ok)
                item = {"run": index, "pass": ok, **result}
                results.append(item)
                if self._journal is not None:
                    self._journal.append("verify_run", {
                        "policy_id": policy_id(model), **item,
                    })
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
        host_id: str,
    ) -> dict[str, Any]:
        target = self._target(target_x)
        assert target is not None
        tolerance = float(tolerance)
        if not 0.0 < tolerance <= 25.0:
            raise ValueError("tolerance must be within (0,25]")
        seconds = self._seconds(max_seconds, name="max_seconds")
        if not self.ensure_model():
            raise ValueError("no compatible Spine checkpoint; train with a verified Motor first")
        player_id = self._require_attached_player(host_id)
        return self._start(
            "run",
            {
                "target_x": target,
                "tolerance": tolerance,
                "max_seconds": seconds,
                "host_id": host_id,
                "player_id": player_id,
            },
            self._run_worker,
            target,
            tolerance,
            seconds,
            host_id,
            player_id,
        )

    def _run_worker(
        self,
        target_x: float,
        tolerance: float,
        max_seconds: float,
        host_id: str,
        player_id: str,
    ) -> None:
        model, _ = model_for_checkpoint(checkpoint_path())
        client = HostClient("gamelab-mcp-run", host_id=host_id)
        try:
            with self._lock:
                self._records["run"]["policy_id"] = policy_id(model)
            runner = GoalRunner(model, client, player_id=player_id)

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
                goals=self._goals,
            )
            with self._lock:
                self._records["run"].update(result)
        finally:
            client.close()


__all__ = ["Laboratory", "LaboratoryBusyError"]
