"""World-tick executor for Spine -> frozen Motor control.

TRAIN exploration belongs to Spine at 10 Hz.  The verified Motor is always
executed deterministically at 60 Hz.  Physics remains authoritative at 120 Hz.
"""
from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Any, Callable

import torch

from .config import MOTOR_HZ, SPINE_HZ, SUCCESS_HOLD_STEPS
from .host import HostError, player_from_state
from .models import SensorHistory, motor_state, sensor_frame
from .motors.continuous import squashed_action
from .reward import RewardConfig, stopped_near_goal_proximity, step_reward

STALE_SECONDS = 0.5
MAX_HOLD_GAP_SECONDS = 0.1
MOTOR_SEND_EPS = 1e-6


class GoalMailbox:
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
    """One Spine decision. action is normalized desired_vx, not motor effort."""

    history: torch.Tensor
    proprioception: torch.Tensor
    action: float
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


@dataclass
class PendingMotor:
    tick: int
    sequence: int
    command_id: int | None
    before_distance: float


class EvidenceError(HostError):
    pass


class EventGuard:
    def __init__(self, client, state: dict) -> None:
        self.client = client
        self.cursor = int((state.get("last_event") or {}).get("event_id", 0))

    def check(self) -> None:
        response = self.client.events(self.cursor, limit=256)
        if response.get("truncated_before") or response.get("has_more"):
            raise EvidenceError("Host event history lost; experiment contaminated")
        for event in response.get("events", []):
            if event.get("kind") in {"input", "reset", "login", "logout"}:
                if event.get("client_id") != self.client.client_id:
                    raise EvidenceError("external Host control; experiment contaminated")
        self.cursor = int(response.get("next_after_event_id", self.cursor))


def _advance_or_wait(client, physics_hz: int) -> None:
    advance = getattr(client, "advance_tick", None)
    if callable(advance):
        advance()
        return
    time.sleep(0.5 / physics_hz)


def _execution_mode(client) -> str:
    return "unpaced" if callable(getattr(client, "advance_tick", None)) else "realtime"


def _finish_decision(
    decision: Decision | None,
    *,
    tick: int,
    hz: int,
    done: bool,
    on_transition: Callable | None,
) -> None:
    if decision is None:
        return
    decision.next_tick = tick
    # Preserve the old discount's physical time base: elapsed Motor intervals.
    decision.elapsed_steps = max(
        1.0,
        (tick - decision.tick) * MOTOR_HZ / hz,
    )
    decision.done = done
    if on_transition is not None:
        on_transition(decision)


def control_loop(
    model,
    client,
    state: dict,
    *,
    target_x: float,
    tolerance: float,
    max_seconds: float,
    sampled: bool = False,
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
    start_tick = snapshot.get("world_tick")
    if (
        not isinstance(epoch, str)
        or type(hz) is not int
        or hz <= 0
        or type(start_tick) is not int
    ):
        raise HostError("Host requires tick/epoch acknowledgement contract; restart backend")
    if hz % MOTOR_HZ != 0 or hz % SPINE_HZ != 0:
        raise HostError("physics_hz must divide exactly into Motor and Spine cadences")

    motor_stride = hz // MOTOR_HZ
    spine_stride = hz // SPINE_HZ
    max_ticks = max(1, int(round(float(max_seconds) * hz)))
    hold_gap_ticks = max(1, int(round(MAX_HOLD_GAP_SECONDS * hz)))

    start_wall = time.monotonic()
    last_fresh_wall = start_wall
    current_state = state
    next_motor_tick = start_tick
    next_spine_tick = start_tick
    last_tick: int | None = None
    stable_since: int | None = None
    revision = 0
    history: SensorHistory | None = None
    cached_goal: torch.Tensor | None = None
    desired_vx = 0.0
    active_decision: Decision | None = None
    pending_motor: PendingMotor | None = None
    best_stopped_proximity = 0.0
    closest_stopped_distance: float | None = None
    steps = spine_calls = requests = duplicates = overruns = 0
    total_reward = 0.0
    result: dict[str, Any] = {}
    reward_config = reward_config or RewardConfig()
    can_relax = True
    mode = _execution_mode(client)

    try:
        while True:
            if cancel is not None and cancel.is_set():
                result["status"] = "cancelled"
                break

            snapshot = current_state.get("snapshot") or {}
            tick = snapshot.get("world_tick")
            if snapshot.get("epoch") != epoch or type(tick) is not int:
                raise EvidenceError("world epoch or tick contract changed")
            if tick < start_tick or (last_tick is not None and tick < last_tick):
                raise EvidenceError("world tick moved backwards")

            if tick < next_motor_tick:
                previous_tick = tick
                _advance_or_wait(client, hz)
                current_state = client.state()
                observed_tick = (current_state.get("snapshot") or {}).get("world_tick")
                now = time.monotonic()
                if observed_tick == previous_tick:
                    duplicates += 1
                    if now - last_fresh_wall > STALE_SECONDS:
                        result["status"] = "stale"
                        break
                elif type(observed_tick) is int and observed_tick > previous_tick:
                    last_fresh_wall = now
                continue

            if tick > next_motor_tick:
                overruns += 1
            next_motor_tick = tick + motor_stride

            guard.check()
            current_identity = (
                current_state["session"].get("session_id"),
                current_state["session"].get("entity_id"),
            )
            if current_identity != identity:
                raise EvidenceError("Host player/session changed")

            player = player_from_state(current_state)
            x = float(player["x"])
            vx = float(player["vx"])
            current_motor = float(player["motor_x"])
            sequence = player.get("last_sequence")
            if type(sequence) is not int:
                raise HostError("missing applied sequence; restart backend")

            new_target, new_revision = goals.read() if goals else (target_x, 1)
            changed = new_revision != revision
            if changed:
                target_x, revision = new_target, new_revision
                stable_since = None
                next_spine_tick = tick
                if history is not None:
                    history.set_target(target_x)

            gap = tick - last_tick if last_tick is not None else 0
            if gap > hold_gap_ticks:
                stable_since = None

            error = target_x - x
            physically_stopped = abs(vx) < 1e-9
            if abs(error) <= tolerance and physically_stopped:
                if stable_since is None:
                    stable_since = tick
            else:
                stable_since = None

            stable_ticks = tick - stable_since if stable_since is not None else 0
            reached = stable_since is not None and stable_ticks >= int(
                round(SUCCESS_HOLD_STEPS * hz / MOTOR_HZ)
            )
            simulated_ticks = tick - start_tick
            timed_out = simulated_ticks >= max_ticks
            simulation_seconds = simulated_ticks / hz
            wall_seconds = time.monotonic() - start_wall

            if pending_motor is not None:
                if sequence < pending_motor.sequence:
                    if timed_out:
                        result["status"] = "unconfirmed"
                        break
                    last_tick = tick
                    _advance_or_wait(client, hz)
                    current_state = client.state()
                    continue
                if sequence != pending_motor.sequence:
                    raise EvidenceError("applied sequence does not match policy")
                if (
                    pending_motor.command_id is not None
                    and player.get("last_input_command_id")
                    != pending_motor.command_id
                ):
                    raise EvidenceError("applied command does not match policy")

                if physically_stopped:
                    distance_now = abs(error)
                    if (
                        closest_stopped_distance is None
                        or distance_now < closest_stopped_distance
                    ):
                        closest_stopped_distance = distance_now
                proximity = stopped_near_goal_proximity(
                    reward_config,
                    distance=abs(error),
                    vx=vx,
                )
                proximity_gain = max(
                    0.0, proximity - best_stopped_proximity
                )
                best_stopped_proximity = max(
                    best_stopped_proximity, proximity
                )
                motor_reward = step_reward(
                    reward_config,
                    before_distance=pending_motor.before_distance,
                    after_distance=abs(error),
                    next_vx=vx,
                    success=reached,
                    timeout=timed_out and not reached,
                    elapsed_steps=(tick - pending_motor.tick) * MOTOR_HZ / hz,
                    stopped_proximity_gain=proximity_gain,
                )
                total_reward += motor_reward
                if active_decision is not None:
                    active_decision.reward += motor_reward
                    active_decision.sequence = sequence
                    active_decision.command_id = pending_motor.command_id
                    active_decision.applied_tick = player.get("last_input_tick")
                pending_motor = None

            result = dict(
                status="reached" if reached else "timeout" if timed_out else "active",
                execution_mode=mode,
                target_x=target_x,
                goal_revision=revision,
                x=x,
                vx=vx,
                motor_x=current_motor,
                desired_vx=desired_vx,
                error=error,
                world_tick=tick,
                start_world_tick=start_tick,
                simulated_ticks=simulated_ticks,
                simulation_seconds=simulation_seconds,
                elapsed_seconds=simulation_seconds,
                wall_seconds=wall_seconds,
                speedup=simulation_seconds / max(wall_seconds, 1e-9),
                epoch=epoch,
                stable_ticks=stable_ticks,
                closest_stopped_distance=closest_stopped_distance,
                best_stopped_proximity=best_stopped_proximity,
                motor_steps=steps,
                spine_calls=spine_calls,
                controller_requests=requests,
                duplicate_snapshots=duplicates,
                overruns=overruns,
                effective_motor_hz=steps / max(simulation_seconds, 1.0 / hz),
                effective_spine_hz=spine_calls / max(simulation_seconds, 1.0 / hz),
                wall_motor_hz=steps / max(wall_seconds, 1e-9),
                wall_spine_hz=spine_calls / max(wall_seconds, 1e-9),
            )

            if on_status is not None:
                on_status(dict(result))
            if reached or timed_out:
                _finish_decision(
                    active_decision,
                    tick=tick,
                    hz=hz,
                    done=True,
                    on_transition=on_transition,
                )
                active_decision = None
                break

            frame = sensor_frame(
                x=x,
                vx=vx,
                motor_x=current_motor,
                target_x=target_x,
            )
            if history is None:
                history = SensorHistory(frame)
            else:
                history.push(frame)
            proprioception = motor_state(vx=vx, motor_x=current_motor)

            if active_decision is None or tick >= next_spine_tick:
                if active_decision is not None:
                    _finish_decision(
                        active_decision,
                        tick=tick,
                        hz=hz,
                        done=False,
                        on_transition=on_transition,
                    )
                latched_history = history.tensor().clone()
                with torch.no_grad():
                    spine_mean, spine_log_std, hidden = model.spine_parameters(
                        latched_history
                    )
                    spine_action, spine_log_prob = squashed_action(
                        spine_mean,
                        spine_log_std,
                        sampled=sampled,
                    )
                    value = model.critic(hidden, proprioception)
                desired_vx = float(spine_action.item())
                cached_goal = model.spine.motor_goal(spine_action)
                active_decision = Decision(
                    history=latched_history,
                    proprioception=proprioception.clone(),
                    action=desired_vx,
                    old_log_prob=float(spine_log_prob.item()),
                    old_value=float(value.item()),
                    tick=tick,
                    sequence=sequence,
                )
                next_spine_tick = tick + spine_stride
                spine_calls += 1

            if cached_goal is None:
                raise RuntimeError("Spine goal was not initialized")

            with torch.no_grad():
                motor_x = float(
                    model.deterministic_motor(
                        cached_goal,
                        proprioception,
                    ).item()
                )

            command_id = None
            command_sequence = sequence
            if abs(motor_x - current_motor) > MOTOR_SEND_EPS:
                response = client.motor(motor_x)
                command_sequence = response["sequence"]
                command_id = response["event"]["command_id"]
                requests += 1

            pending_motor = PendingMotor(
                tick=tick,
                sequence=command_sequence,
                command_id=command_id,
                before_distance=abs(error),
            )
            steps += 1
            last_tick = tick
            _advance_or_wait(client, hz)
            current_state = client.state()

    except EvidenceError as exc:
        can_relax = False
        result.update(status="contaminated", error_detail=str(exc))
    finally:
        if can_relax:
            try:
                guard.check()
                end = client.state()
                if (
                    end["session"].get("session_id"),
                    end["session"].get("entity_id"),
                ) == identity:
                    if (
                        abs(float(player_from_state(end)["motor_x"]))
                        > MOTOR_SEND_EPS
                    ):
                        client.motor(0.0)
                        advance = getattr(client, "advance_tick", None)
                        if callable(advance):
                            advance()
            except HostError:
                pass

    final_wall = time.monotonic() - start_wall
    final_tick = int(result.get("world_tick", start_tick))
    simulation_seconds = max(0, final_tick - start_tick) / hz
    result.update(
        reward=total_reward,
        motor_steps=steps,
        spine_calls=spine_calls,
        controller_requests=requests,
        duplicate_snapshots=duplicates,
        overruns=overruns,
        closest_stopped_distance=closest_stopped_distance,
        best_stopped_proximity=best_stopped_proximity,
        desired_vx=desired_vx,
        execution_mode=mode,
        simulated_ticks=max(0, final_tick - start_tick),
        simulation_seconds=simulation_seconds,
        elapsed_seconds=simulation_seconds,
        wall_seconds=final_wall,
        speedup=simulation_seconds / max(final_wall, 1e-9),
        effective_motor_hz=steps / max(simulation_seconds, 1.0 / hz),
        effective_spine_hz=spine_calls / max(simulation_seconds, 1.0 / hz),
        wall_motor_hz=steps / max(final_wall, 1e-9),
        wall_spine_hz=spine_calls / max(final_wall, 1e-9),
    )
    return result
