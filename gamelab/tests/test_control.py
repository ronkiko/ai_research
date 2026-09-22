from __future__ import annotations

import unittest
from unittest.mock import patch

import torch

from gamelab.control import GoalMailbox, control_loop


class Clock:
    now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, duration):
        self.now += duration


class StopModel:
    def eval(self):
        pass

    def spine(self, history):
        return torch.zeros(4), torch.zeros(16)

    def motor(self, goal, proprioception):
        return torch.tensor([-100.0, 100.0, -100.0])

    def critic(self, hidden, proprioception):
        return torch.tensor(0.0)

    def action_to_move(self, action):
        return (-1, 0, 1)[action]


class Client:
    client_id = "test-model"

    def __init__(self, frozen=False, external=False):
        self.tick = 0
        self.frozen, self.external = frozen, external
        self.inputs = []

    def state(self):
        self.tick += 0 if self.frozen else 2
        return {
            "session": {"session_id": "s", "entity_id": "p"},
            "last_event": {"event_id": 0},
            "snapshot": {"epoch": "e", "world_tick": self.tick, "physics_hz": 120,
                         "entities": [{"entity_id": "p", "x": 100, "vx": 0,
                                       "move_x": 0, "last_sequence": 0,
                                       "last_input_tick": 0}]},
        }

    def events(self, cursor, limit=256):
        return {"next_after_event_id": cursor, "events": [
            {"kind": "input", "client_id": "human"}
        ] if self.external else []}

    def input(self, move):
        self.inputs.append(move)
        raise AssertionError("STOP policy must not emit repeated STOP commands")


class ControlTests(unittest.TestCase):
    def run_loop(self, client, **kwargs):
        clock = Clock()
        with patch("gamelab.control.time.monotonic", clock.monotonic), patch(
            "gamelab.control.time.sleep", clock.sleep
        ):
            return control_loop(StopModel(), client, client.state(),
                                target_x=kwargs.pop("target_x", 100), tolerance=1,
                                max_seconds=kwargs.pop("max_seconds", 0.8), **kwargs)

    def test_frozen_tick_never_proves_success(self):
        transitions = []
        result = self.run_loop(Client(frozen=True), on_transition=transitions.append)
        self.assertEqual(result["status"], "stale")
        self.assertEqual(transitions, [])
        self.assertEqual(result["motor_steps"], 1)

    def test_success_requires_world_time_and_spine_is_cached(self):
        result = self.run_loop(Client())
        self.assertEqual(result["status"], "reached")
        self.assertGreaterEqual(result["stable_ticks"], 12)
        self.assertLess(result["spine_calls"], result["motor_steps"])

    def test_external_control_invalidates_run(self):
        result = self.run_loop(Client(external=True))
        self.assertEqual(result["status"], "contaminated")
        self.assertEqual(result["motor_steps"], 0)

    def test_live_goal_revision_is_applied_without_reset(self):
        goals = GoalMailbox(900)

        def update(status):
            if status["motor_steps"] == 2:
                goals.update(100)

        result = self.run_loop(Client(), target_x=900, goals=goals, on_status=update)
        self.assertEqual(result["status"], "reached")
        self.assertEqual(result["goal_revision"], 2)

    def test_sampled_and_greedy_share_time_and_observations(self):
        train, run = [], []
        self.run_loop(Client(), sampled=True, on_transition=train.append)
        self.run_loop(Client(), on_transition=run.append)
        self.assertEqual([(t.tick, t.next_tick, t.action) for t in train],
                         [(t.tick, t.next_tick, t.action) for t in run])
        self.assertTrue(all(t.next_tick > t.tick for t in train))

    def test_lost_events_invalidate_even_a_stopped_player(self):
        client = Client()
        client.events = lambda *args, **kwargs: {"truncated_before": True}
        result = self.run_loop(client)
        self.assertEqual(result["status"], "contaminated")

    def test_short_timeout_does_not_validate_unobserved_transition(self):
        result = self.run_loop(Client(frozen=True), max_seconds=0.2)
        self.assertEqual(result["status"], "unconfirmed")

    def test_delayed_input_is_acknowledged_before_next_decision(self):
        class RightModel(StopModel):
            def motor(self, goal, proprioception):
                return torch.tensor([-100.0, -100.0, 100.0])

        class DelayedClient(Client):
            def __init__(self):
                super().__init__()
                self.apply_at = None

            def input(self, move):
                self.inputs.append(move)
                self.apply_at = self.tick + 6
                return {"sequence": 1, "event": {"command_id": 9}}

            def state(self):
                state = super().state()
                if self.apply_at is not None and self.tick >= self.apply_at:
                    state["snapshot"]["entities"][0].update(
                        last_sequence=1, last_input_command_id=9,
                        last_input_tick=self.apply_at, move_x=1, vx=180,
                    )
                return state

        clock, client, transitions = Clock(), DelayedClient(), []
        with patch("gamelab.control.time.monotonic", clock.monotonic), patch(
            "gamelab.control.time.sleep", clock.sleep
        ):
            control_loop(RightModel(), client, client.state(), target_x=900,
                         tolerance=1, max_seconds=0.2, on_transition=transitions.append)
        first = transitions[0]
        self.assertEqual(first.command_id, 9)
        self.assertEqual(first.next_tick - first.tick, 6)
        self.assertEqual(first.applied_tick, first.next_tick)
        self.assertEqual(first.elapsed_steps, 3)
        self.assertEqual(client.inputs, [1, 0])  # Policy input, then terminal safety.


if __name__ == "__main__":
    unittest.main()
