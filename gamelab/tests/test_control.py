from __future__ import annotations

import unittest
from unittest.mock import patch

import torch

from organism.control import GoalMailbox, control_loop


class Clock:
    now = 0.0
    def monotonic(self): return self.now
    def sleep(self, duration): self.now += duration


class FixedMotor:
    def __init__(self, mean: float):
        self.mean = mean
    def parameters_for(self, goal, proprioception):
        return torch.tensor(self.mean), torch.tensor(-20.0)


class FakeSpine:
    @staticmethod
    def motor_goal(desired_vx):
        value = desired_vx.reshape(1)
        return torch.cat((value, torch.zeros(3)))


class StopModel:
    def __init__(self):
        self.motor = FixedMotor(0.0)
        self.spine = FakeSpine()
    def eval(self): pass
    def spine_parameters(self, history, input_delay=0.):
        return torch.tensor(0.0), torch.tensor(-20.0), torch.zeros(16)
    def deterministic_motor(self, goal, proprioception):
        mean, _ = self.motor.parameters_for(goal, proprioception)
        return torch.tanh(mean)
    def critic(self, hidden, proprioception): return torch.tensor(0.0)


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
            "snapshot": {
                "epoch": "e", "world_tick": self.tick, "physics_hz": 120,
                "entities": [{
                    "entity_id": "p", "x": 100.0, "vx": 0.0, "motor_x": 0.0,
                    "last_sequence": 0, "last_input_tick": 0,
                }],
            },
        }
    def events(self, cursor, limit=256):
        return {"next_after_event_id": cursor, "events": [
            {"kind": "input", "client_id": "human"}
        ] if self.external else []}
    def motor(self, value):
        self.inputs.append(value)
        raise AssertionError("zero deterministic Motor must not emit commands")


class ControlTests(unittest.TestCase):
    def run_loop(self, client, **kwargs):
        clock = Clock()
        with patch("organism.control.time.monotonic", clock.monotonic), patch(
            "organism.control.time.sleep", clock.sleep
        ):
            return control_loop(
                StopModel(), client, client.state(),
                target_x=kwargs.pop("target_x", 100), tolerance=0.9,
                max_seconds=kwargs.pop("max_seconds", 0.8), **kwargs
            )

    def test_frozen_tick_never_proves_success(self):
        transitions = []
        result = self.run_loop(Client(frozen=True), on_transition=transitions.append)
        self.assertEqual(result["status"], "stale")
        self.assertEqual(transitions, [])
        self.assertEqual(result["motor_steps"], 1)

    def test_success_requires_world_time_and_spine_is_slower_than_motor(self):
        transitions = []
        result = self.run_loop(Client(), on_transition=transitions.append)
        self.assertEqual(result["status"], "reached")
        self.assertGreaterEqual(result["stable_ticks"], 12)
        self.assertLess(result["spine_calls"], result["motor_steps"])
        self.assertEqual(len(transitions), result["spine_calls"])

    def test_external_control_invalidates_run(self):
        result = self.run_loop(Client(external=True))
        self.assertEqual(result["status"], "contaminated")

    def test_live_goal_revision_is_applied_without_reset(self):
        goals = GoalMailbox(900)
        def update(status):
            if status["motor_steps"] == 2:
                goals.update(100)
        result = self.run_loop(Client(), target_x=900, goals=goals, on_status=update)
        self.assertEqual(result["status"], "reached")
        self.assertEqual(result["goal_revision"], 2)

    def test_frozen_world_never_consumes_virtual_episode_time(self):
        result = self.run_loop(Client(frozen=True), max_seconds=0.2)
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["simulation_seconds"], 0.0)

    def test_spine_transition_spans_six_motor_intervals_but_one_discount_step(self):
        class RightModel(StopModel):
            def __init__(self):
                super().__init__()
                self.motor = FixedMotor(8.0)
                self.delays = []
            def spine_parameters(self, history, input_delay=0.):
                self.delays.append(input_delay)
                return super().spine_parameters(history, input_delay)

        class DelayedClient(Client):
            def __init__(self):
                super().__init__()
                self.apply_at = None
                self.sequence = 0
                self.applied = 0
            def motor(self, value):
                self.inputs.append(value)
                self.sequence += 1
                self.apply_at = self.tick + 2
                return {"sequence": self.sequence, "event": {"command_id": self.sequence}}
            def state(self):
                state = super().state()
                if self.apply_at is not None and self.tick >= self.apply_at:
                    self.applied = self.sequence
                    state["snapshot"]["entities"][0].update(
                        last_sequence=self.applied,
                        last_input_command_id=self.applied,
                        last_input_tick=self.apply_at,
                        motor_x=self.inputs[-1],
                        vx=10.0,
                    )
                return state

        clock, client, transitions = Clock(), DelayedClient(), []
        model = RightModel()
        with patch("organism.control.time.monotonic", clock.monotonic), patch(
            "organism.control.time.sleep", clock.sleep
        ):
            result = control_loop(
                model, client, client.state(), target_x=900,
                tolerance=0.9, max_seconds=0.25, on_transition=transitions.append
            )
        self.assertTrue(transitions)
        first = transitions[0]
        self.assertEqual(first.next_tick - first.tick, 12)
        self.assertEqual(first.elapsed_steps, 1)
        self.assertGreater(result["motor_steps"], result["spine_calls"])
        self.assertEqual(model.delays[0], 0.)
        self.assertIn(.5, model.delays[1:])
        self.assertIn(.5, [t.input_delay for t in transitions])


if __name__ == "__main__":
    unittest.main()
