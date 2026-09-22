"""One measured realtime loop for sampled TRAIN and frozen VERIFY/RUN.

Scheduling, evidence and terminal stops are infrastructure, never steering.
"""
from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Any, Callable

import torch
from torch.distributions import Categorical

from .config import MOTOR_HZ, SPINE_HZ, SUCCESS_HOLD_STEPS
from .host import HostError, player_from_state
from .models import SensorHistory, motor_state, sensor_frame
from .reward import RewardConfig, step_reward

STALE_SECONDS = 0.5
MAX_HOLD_GAP_SECONDS = 0.1


class GoalMailbox:
    """One current strategic goal, not a queued movement plan."""

    def __init__(self, target_x: float) -> None:
        self._lock = threading.Lock()
        self._goal = (float(target_x), 1)

    def update(self, target_x: float) -> int:
        with self._lock:
            self._goal = (float(target_x), self._goal[1] + 1)
            return self._goal[1]

    def read(self) -> tuple[float, int]:
        with self._lock:
            return self._goal


@dataclass
class Decision:
    history: torch.Tensor
    proprioception: torch.Tensor
    action: int
    old_log_prob: float
    old_value: float
    reward: float = 0.0
    done: bool = False
    tick: int = 0
    next_tick: int = 0
    elapsed_steps: float = 1.0
    sequence: int = 0
    command_id: int | None = None
    applied_tick: int | None = None


class EvidenceError(HostError):
    pass


class EventGuard:
    def __init__(self, client, state: dict) -> None:
        self.client = client
        self.cursor = int((state.get("last_event") or {}).get("event_id", 0))

    def check(self) -> None:
        # Bound work. An overflowing event stream invalidates the experiment.
        response = self.client.events(self.cursor, limit=256)
        if response.get("truncated_before") or response.get("has_more"):
            raise EvidenceError("Host event history lost; experiment contaminated")
        for event in response.get("events", []):
            if event.get("kind") in {"input", "reset", "login", "logout"}:
                if event.get("client_id") != self.client.client_id:
                    raise EvidenceError("external Host control; experiment contaminated")
        self.cursor = int(response.get("next_after_event_id", self.cursor))


def control_loop(
    model, client, state: dict, *, target_x: float, tolerance: float,
    max_seconds: float, sampled: bool = False,
    reward_config: RewardConfig | None = None,
    cancel: threading.Event | None = None,
    goals: GoalMailbox | None = None,
    on_status: Callable | None = None,
    on_transition: Callable | None = None,
) -> dict[str, Any]:
    model.eval()
    guard = EventGuard(client, state)
    session = state["session"]
    identity = (session.get("session_id"), session.get("entity_id"))
    snapshot = state["snapshot"]
    epoch = snapshot.get("epoch")
    hz = snapshot.get("physics_hz")
    if not isinstance(epoch, str) or type(hz) is not int or hz <= 0:
        raise HostError("Host requires tick/epoch acknowledgement contract; restart backend")
    start = time.monotonic()
    last_fresh = start
    next_motor = start
    next_spine = start
    last_tick: int | None = None
    stable_since: int | None = None
    stable_sequence: int | None = None
    revision = 0
    history = None
    cached_goal = hidden = latched_history = None
    pending = None
    before_distance = 0.0
    steps = spine_calls = requests = duplicates = overruns = 0
    total_reward = 0.0
    result: dict[str, Any] = {}
    reward_config = reward_config or RewardConfig()
    can_stop = True
    try:
        while True:
            now = time.monotonic()
            if cancel is not None and cancel.is_set():
                result["status"] = "cancelled"
                break
            if now < next_motor:
                time.sleep(next_motor - now)
            now = time.monotonic()
            if now - next_motor >= 1.0 / MOTOR_HZ:
                overruns += 1
            # Drop missed slots. Never replay a backlog of actuator decisions.
            next_motor = now + 1.0 / MOTOR_HZ
            state = client.state()
            guard.check()
            current_identity = (state["session"].get("session_id"), state["session"].get("entity_id"))
            if current_identity != identity:
                raise EvidenceError("Host player/session changed")
            snapshot = state["snapshot"]
            tick = snapshot.get("world_tick")
            if snapshot.get("epoch") != epoch or type(tick) is not int:
                raise EvidenceError("world epoch or tick contract changed")
            now = time.monotonic()
            if last_tick is not None and tick < last_tick:
                raise EvidenceError("world tick moved backwards")
            if now - last_fresh > STALE_SECONDS:
                result["status"] = "stale"
                break
            if tick == last_tick:
                duplicates += 1
                if now - start >= max_seconds:
                    result["status"] = "unconfirmed" if pending is not None else "timeout"
                    break
                continue
            last_fresh = now
            player = player_from_state(state)
            x, vx, current_move = float(player["x"]), float(player["vx"]), int(player["move_x"])
            sequence = player.get("last_sequence")
            if type(sequence) is not int:
                raise HostError("missing applied sequence; restart backend")
            new_target, new_revision = goals.read() if goals else (target_x, 1)
            changed = new_revision != revision
            if changed:
                target_x, revision = new_target, new_revision
                stable_since = stable_sequence = None
                next_spine = now
                if history is not None:
                    history.set_target(target_x)
            gap = tick - last_tick if last_tick is not None else 0
            if gap > hz * MAX_HOLD_GAP_SECONDS:
                stable_since = None
            error = target_x - x
            if abs(error) <= tolerance and abs(vx) < 1e-9 and current_move == 0:
                if stable_since is None or stable_sequence != sequence:
                    stable_since, stable_sequence = tick, sequence
            else:
                stable_since = stable_sequence = None
            stable_ticks = tick - stable_since if stable_since is not None else 0
            reached = stable_since is not None and stable_ticks / hz >= SUCCESS_HOLD_STEPS / MOTOR_HZ
            timed_out = now - start >= max_seconds
            result = dict(
                status="reached" if reached else "timeout" if timed_out else "active",
                target_x=target_x, goal_revision=revision, x=x, vx=vx, move_x=current_move,
                error=error, world_tick=tick, epoch=epoch, stable_ticks=stable_ticks,
                motor_steps=steps, spine_calls=spine_calls, controller_requests=requests,
                duplicate_snapshots=duplicates, overruns=overruns,
                elapsed_seconds=now-start, effective_motor_hz=steps/max(now-start, 1e-9),
                effective_spine_hz=spine_calls/max(now-start, 1e-9),
            )
            if pending is not None:
                if sequence < pending.sequence:
                    # A queued input is not yet applied. Keep observing, do not
                    # invent another action or reward for a repeated decision.
                    if now - start >= max_seconds:
                        result["status"] = "unconfirmed"
                        break
                    last_tick = tick
                    continue
                if sequence != pending.sequence:
                    raise EvidenceError("applied sequence does not match policy")
                if pending.command_id is not None and player.get("last_input_command_id") != pending.command_id:
                    raise EvidenceError("applied command does not match policy")
                pending.next_tick = tick
                pending.elapsed_steps = (tick - pending.tick) * MOTOR_HZ / hz
                pending.applied_tick = player.get("last_input_tick")
                pending.reward = step_reward(
                    reward_config, before_distance=before_distance,
                    after_distance=abs(error), next_vx=vx, next_move_x=current_move,
                    success=reached, timeout=timed_out and not reached,
                    elapsed_steps=pending.elapsed_steps,
                )
                pending.done = reached or timed_out
                total_reward += pending.reward
                if on_transition is not None:
                    on_transition(pending)
                pending = None
            if on_status is not None:
                on_status(dict(result))
            if reached or timed_out:
                break
            frame = sensor_frame(x=x, vx=vx, move_x=current_move, target_x=target_x)
            if history is None:
                history = SensorHistory(frame)
            else:
                history.push(frame)
            proprioception = motor_state(vx=vx, move_x=current_move)
            with torch.no_grad():
                if cached_goal is None or now >= next_spine:
                    latched_history = history.tensor().clone()
                    cached_goal, hidden = model.spine(latched_history)
                    next_spine = now + 1.0 / SPINE_HZ
                    spine_calls += 1
                logits = model.motor(cached_goal, proprioception)
                distribution = Categorical(logits=logits)
                action_tensor = distribution.sample() if sampled else logits.argmax()
                action = int(action_tensor.item())
                value = model.critic(hidden, proprioception)
            move_x = model.action_to_move(action)
            command_id = None
            if move_x != current_move:
                response = client.input(move_x)
                sequence = response["sequence"]
                command_id = response["event"]["command_id"]
                requests += 1
            pending = Decision(
                latched_history.clone(), proprioception.clone(), action,
                float(distribution.log_prob(action_tensor)), float(value),
                tick=tick, sequence=sequence, command_id=command_id,
            )
            before_distance = abs(error)
            steps += 1
            last_tick = tick
    except EvidenceError as exc:
        can_stop = False  # Do not overwrite an intervening controller's input.
        result.update(status="contaminated", error_detail=str(exc))
    finally:
        if can_stop:
            try:
                guard.check()
                end = client.state()
                if (end["session"].get("session_id"), end["session"].get("entity_id")) == identity:
                    if int(player_from_state(end)["move_x"]) != 0:
                        client.input(0)
            except HostError:
                pass
    result.update(reward=total_reward, motor_steps=steps, spine_calls=spine_calls,
                  controller_requests=requests, duplicate_snapshots=duplicates,
                  overruns=overruns)
    elapsed = time.monotonic() - start
    result.update(elapsed_seconds=elapsed,
                  effective_motor_hz=steps / max(elapsed, 1e-9),
                  effective_spine_hz=spine_calls / max(elapsed, 1e-9))
    return result
