"""One world-tick executor for realtime and unpaced TRAIN/VERIFY/RUN.

The learned policy always lives on GameServer time: physics ticks schedule Motor,
Spine, timeout, reward duration and success hold.  Realtime waits for those
authoritative ticks; unpaced advances the same ZoneRuntime without wall-clock
sleep.  Wall time is only liveness/diagnostic instrumentation.
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
from .reward import RewardConfig, stopped_near_goal_proximity, step_reward

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
        response = self.client.events(self.cursor, limit=256)
        if response.get("truncated_before") or response.get("has_more"):
            raise EvidenceError("Host event history lost; experiment contaminated")
        for event in response.get("events", []):
            if event.get("kind") in {"input", "reset", "login", "logout"}:
                if event.get("client_id") != self.client.client_id:
                    raise EvidenceError("external Host control; experiment contaminated")
        self.cursor = int(response.get("next_after_event_id", self.cursor))


def _advance_or_wait(client, physics_hz: int) -> None:
    """Progress one opportunity for a fresh authoritative world tick."""
    advance = getattr(client, "advance_tick", None)
    if callable(advance):
        advance()
        return
    time.sleep(0.5 / physics_hz)


def _execution_mode(client) -> str:
    return "unpaced" if callable(getattr(client, "advance_tick", None)) else "realtime"


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
    stable_sequence: int | None = None
    revision = 0
    history = None
    cached_goal = hidden = latched_history = None
    pending = None
    before_distance = 0.0
    best_stopped_proximity = 0.0
    closest_stopped_distance: float | None = None
    steps = spine_calls = requests = duplicates = overruns = 0
    total_reward = 0.0
    result: dict[str, Any] = {}
    reward_config = reward_config or RewardConfig()
    can_stop = True
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

            # Motor/Spine cadence is defined only by authoritative world ticks.
            # Realtime sleeps until the server advances; unpaced explicitly ticks
            # the same ZoneRuntime.  No policy work happens on duplicate snapshots.
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
            current_move = int(player["move_x"])
            sequence = player.get("last_sequence")
            if type(sequence) is not int:
                raise HostError("missing applied sequence; restart backend")

            new_target, new_revision = goals.read() if goals else (target_x, 1)
            changed = new_revision != revision
            if changed:
                target_x, revision = new_target, new_revision
                stable_since = stable_sequence = None
                next_spine_tick = tick
                if history is not None:
                    history.set_target(target_x)

            gap = tick - last_tick if last_tick is not None else 0
            if gap > hold_gap_ticks:
                stable_since = None

            error = target_x - x
            if abs(error) <= tolerance and abs(vx) < 1e-9 and current_move == 0:
                if stable_since is None or stable_sequence != sequence:
                    stable_since, stable_sequence = tick, sequence
            else:
                stable_since = stable_sequence = None

            stable_ticks = tick - stable_since if stable_since is not None else 0
            reached = stable_since is not None and stable_ticks >= int(
                round(SUCCESS_HOLD_STEPS * hz / MOTOR_HZ)
            )
            simulated_ticks = tick - start_tick
            timed_out = simulated_ticks >= max_ticks
            simulation_seconds = simulated_ticks / hz
            wall_seconds = time.monotonic() - start_wall

            result = dict(
                status="reached" if reached else "timeout" if timed_out else "active",
                execution_mode=mode,
                target_x=target_x,
                goal_revision=revision,
                x=x,
                vx=vx,
                move_x=current_move,
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

            if pending is not None:
                if sequence < pending.sequence:
                    if timed_out:
                        result["status"] = "unconfirmed"
                        break
                    last_tick = tick
                    _advance_or_wait(client, hz)
                    current_state = client.state()
                    continue
                if sequence != pending.sequence:
                    raise EvidenceError("applied sequence does not match policy")
                if (
                    pending.command_id is not None
                    and player.get("last_input_command_id") != pending.command_id
                ):
                    raise EvidenceError("applied command does not match policy")
                pending.next_tick = tick
                pending.elapsed_steps = (tick - pending.tick) * MOTOR_HZ / hz
                pending.applied_tick = player.get("last_input_tick")
                stopped_now = abs(vx) < 1e-9 and current_move == 0
                if stopped_now:
                    distance_now = abs(error)
                    if closest_stopped_distance is None or distance_now < closest_stopped_distance:
                        closest_stopped_distance = distance_now
                proximity = stopped_near_goal_proximity(
                    reward_config,
                    distance=abs(error),
                    vx=vx,
                    move_x=current_move,
                )
                proximity_gain = max(0.0, proximity - best_stopped_proximity)
                best_stopped_proximity = max(best_stopped_proximity, proximity)
                pending.reward = step_reward(
                    reward_config,
                    before_distance=before_distance,
                    after_distance=abs(error),
                    next_vx=vx,
                    next_move_x=current_move,
                    success=reached,
                    timeout=timed_out and not reached,
                    elapsed_steps=pending.elapsed_steps,
                    stopped_proximity_gain=proximity_gain,
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

            frame = sensor_frame(
                x=x,
                vx=vx,
                move_x=current_move,
                target_x=target_x,
            )
            if history is None:
                history = SensorHistory(frame)
            else:
                history.push(frame)
            proprioception = motor_state(vx=vx, move_x=current_move)

            with torch.no_grad():
                if cached_goal is None or tick >= next_spine_tick:
                    latched_history = history.tensor().clone()
                    cached_goal, hidden = model.spine(latched_history)
                    next_spine_tick = tick + spine_stride
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
                latched_history.clone(),
                proprioception.clone(),
                action,
                float(distribution.log_prob(action_tensor)),
                float(value),
                tick=tick,
                sequence=sequence,
                command_id=command_id,
            )
            before_distance = abs(error)
            steps += 1
            last_tick = tick
            _advance_or_wait(client, hz)
            current_state = client.state()

    except EvidenceError as exc:
        can_stop = False
        result.update(status="contaminated", error_detail=str(exc))
    finally:
        if can_stop:
            try:
                guard.check()
                end = client.state()
                if (
                    end["session"].get("session_id"),
                    end["session"].get("entity_id"),
                ) == identity:
                    if int(player_from_state(end)["move_x"]) != 0:
                        client.input(0)
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
