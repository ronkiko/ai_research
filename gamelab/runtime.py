"""Realtime learned controller: strategic target -> Spine -> one Motor."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable

import torch

from .config import (
    DEFAULT_GOAL_TIMEOUT,
    MOTOR_HZ,
    SPINE_PERIOD_MOTOR_STEPS,
    SUCCESS_HOLD_STEPS,
    SUCCESS_TOLERANCE,
    WORLD_MAX_X,
    WORLD_MIN_X,
)
from .host import HostClient, HostError, player_from_state
from .models import (
    SensorHistory,
    SpineMotorPolicy,
    load_checkpoint,
    motor_state,
    sensor_frame,
)


DEFAULT_CHECKPOINT = Path(__file__).resolve().parent / "runtime" / "spine_motor.pt"


def checkpoint_path() -> Path:
    value = os.environ.get("GAMELAB_CHECKPOINT")
    return Path(value) if value else DEFAULT_CHECKPOINT


def wait_player(client: HostClient, timeout: float = 2.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return client.state()
        except HostError as exc:
            last_error = exc
            time.sleep(0.02)
    raise HostError(f"player did not become observable: {last_error}")


def ensure_player(client: HostClient, player_id: str) -> dict[str, Any]:
    try:
        session = client.session()
    except HostError:
        client.login(player_id)
        return wait_player(client)
    current = session.get("player_id")
    if current != player_id:
        raise HostError(
            f"GameClient Host already owns {current}; expected {player_id}"
        )
    return wait_player(client)


def reset_player(client: HostClient, player_id: str) -> dict[str, Any]:
    """Training/verification reset. Not part of learned movement control."""
    try:
        client.logout()
    except HostError:
        pass
    client.login(player_id)
    return wait_player(client)


class GoalRunner:
    """Runs only learned inference at 10 Hz Spine / 60 Hz Motor."""

    def __init__(
        self,
        model: SpineMotorPolicy,
        client: HostClient,
        *,
        player_id: str = "player1",
    ) -> None:
        self.model = model
        self.model.eval()
        self.client = client
        self.player_id = player_id

    def run(
        self,
        target_x: float,
        *,
        tolerance: float = SUCCESS_TOLERANCE,
        max_seconds: float = DEFAULT_GOAL_TIMEOUT,
        cancel: threading.Event | None = None,
        on_status: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        target_x = float(target_x)
        tolerance = float(tolerance)
        max_seconds = float(max_seconds)
        if not WORLD_MIN_X <= target_x <= WORLD_MAX_X:
            raise ValueError("target_x must be within [0,1000]")
        if not 0.0 < tolerance <= 25.0:
            raise ValueError("tolerance must be within (0,25]")
        if not 0.1 <= max_seconds <= 120.0:
            raise ValueError("max_seconds must be within [0.1,120]")

        state = ensure_player(self.client, self.player_id)
        player = player_from_state(state)
        first = sensor_frame(
            x=player["x"],
            vx=player["vx"],
            move_x=player["move_x"],
            target_x=target_x,
        )
        history = SensorHistory(first)
        cached_goal: torch.Tensor | None = None
        start = time.monotonic()
        deadline = start
        step = 0
        hold = 0
        last_status: dict[str, Any] = {}

        try:
            while True:
                state = self.client.state()
                player = player_from_state(state)
                x = float(player["x"])
                vx = float(player["vx"])
                current_move = int(player["move_x"])
                error = target_x - x

                if abs(error) <= tolerance and abs(vx) < 1e-9 and current_move == 0:
                    hold += 1
                else:
                    hold = 0

                last_status = {
                    "status": "active",
                    "target_x": target_x,
                    "x": x,
                    "vx": vx,
                    "move_x": current_move,
                    "error": error,
                    "motor_steps": step,
                    "stable_steps": hold,
                }
                if on_status is not None:
                    on_status(dict(last_status))

                if hold >= SUCCESS_HOLD_STEPS:
                    return {**last_status, "status": "reached"}

                if cancel is not None and cancel.is_set():
                    return {**last_status, "status": "cancelled"}

                if time.monotonic() - start >= max_seconds:
                    return {**last_status, "status": "timeout"}

                frame = sensor_frame(
                    x=x,
                    vx=vx,
                    move_x=current_move,
                    target_x=target_x,
                )
                history.push(frame)
                if cached_goal is None or step % SPINE_PERIOD_MOTOR_STEPS == 0:
                    with torch.no_grad():
                        cached_goal, _ = self.model.spine(history.tensor())

                proprioception = motor_state(vx=vx, move_x=current_move)
                with torch.no_grad():
                    logits = self.model.motor(cached_goal, proprioception)
                    action = int(logits.argmax().item())
                move_x = self.model.action_to_move(action)
                if move_x != current_move:
                    self.client.input(move_x)

                step += 1
                deadline += 1.0 / MOTOR_HZ
                delay = deadline - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
        finally:
            # Terminal/cancel safety only. It is not used to reach the goal.
            try:
                state = self.client.state()
                player = player_from_state(state)
                if int(player["move_x"]) != 0:
                    self.client.input(0)
            except HostError:
                pass


class GoalRuntime:
    """Threaded wrapper used by the goal-level MCP server."""

    def __init__(self, *, player_id: str = "player1", path: Path | None = None) -> None:
        self.player_id = player_id
        self.path = path or checkpoint_path()
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._status: dict[str, Any] = {"status": "idle"}
        self._model: SpineMotorPolicy | None = None

    @property
    def model_ready(self) -> bool:
        return self.path.is_file()

    def _load_model(self) -> SpineMotorPolicy:
        model = SpineMotorPolicy()
        load_checkpoint(self.path, model)
        model.eval()
        return model

    def start_goal(
        self,
        target_x: float,
        *,
        tolerance: float = SUCCESS_TOLERANCE,
        max_seconds: float = DEFAULT_GOAL_TIMEOUT,
    ) -> dict[str, Any]:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("a GameLab motor goal is already active")
            if not self.model_ready:
                raise RuntimeError("GameLab model checkpoint is missing; train first")
            self._cancel = threading.Event()
            self._model = self._load_model()
            self._status = {
                "status": "starting",
                "target_x": float(target_x),
                "tolerance": float(tolerance),
            }
            thread = threading.Thread(
                target=self._worker,
                args=(float(target_x), float(tolerance), float(max_seconds)),
                daemon=True,
                name="gamelab-goal",
            )
            self._thread = thread
            thread.start()
            return dict(self._status)

    def _worker(self, target_x: float, tolerance: float, max_seconds: float) -> None:
        client = HostClient("gamelab-motor")
        try:
            assert self._model is not None
            runner = GoalRunner(self._model, client, player_id=self.player_id)

            def update(status: dict[str, Any]) -> None:
                with self._lock:
                    self._status = status

            result = runner.run(
                target_x,
                tolerance=tolerance,
                max_seconds=max_seconds,
                cancel=self._cancel,
                on_status=update,
            )
            with self._lock:
                self._status = result
        except Exception as exc:
            with self._lock:
                self._status = {
                    "status": "failed",
                    "target_x": target_x,
                    "error": str(exc),
                }
        finally:
            client.close()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def cancel(self) -> dict[str, Any]:
        with self._lock:
            running = self._thread is not None and self._thread.is_alive()
            if running:
                self._cancel.set()
            return {
                "accepted": bool(running),
                "status": self._status.get("status"),
            }


def load_runtime_model(path: Path | None = None) -> SpineMotorPolicy:
    actual = path or checkpoint_path()
    model = SpineMotorPolicy()
    load_checkpoint(actual, model)
    model.eval()
    return model


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run learned GameLab goal")
    parser.add_argument("--target", type=float, required=True)
    parser.add_argument("--player", default="player1")
    parser.add_argument("--tolerance", type=float, default=SUCCESS_TOLERANCE)
    parser.add_argument("--timeout", type=float, default=DEFAULT_GOAL_TIMEOUT)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args(argv)

    client = HostClient("gamelab-run")
    try:
        if args.reset:
            reset_player(client, args.player)
        model = load_runtime_model()
        result = GoalRunner(model, client, player_id=args.player).run(
            args.target,
            tolerance=args.tolerance,
            max_seconds=args.timeout,
        )
        print(json.dumps(result, sort_keys=True))
        return 0 if result.get("status") == "reached" else 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
