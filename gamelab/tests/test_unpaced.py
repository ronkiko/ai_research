from __future__ import annotations

import unittest

import torch

from gamelab.control import control_loop
from gamelab.host import player_from_state
from gamelab.runtime import ensure_player, reset_player_state
from gamelab.unpaced import UnpacedHostClient
from gameserver.v1.zone.model import ZoneRuntime


class RuleMotor:
    def parameters_for(self, goal, proprioception):
        # goal[0] is normalized desired velocity, proprioception[0] is vx/180.
        effort = torch.clamp(50.0 * goal[0] - 1.8 * proprioception[0], -0.999, 0.999)
        mean = torch.atanh(effort)
        return mean, torch.tensor(-20.0)


class RuleSpine:
    @staticmethod
    def motor_goal(desired_vx):
        return torch.cat((desired_vx.reshape(1), torch.zeros(3)))


class RuleModel:
    def __init__(self):
        self.motor = RuleMotor()
        self.spine = RuleSpine()
    def eval(self): pass
    def spine_parameters(self, history):
        signal = torch.clamp(history[3, -1], -0.999, 0.999)
        # Test-only convergent rule for the bounded local goal encoding:
        # fast while far, quadratically gentle as dx approaches zero.
        desired = 0.8 * signal * torch.abs(signal)
        return torch.atanh(desired), torch.tensor(-20.0), torch.zeros(16)
    def deterministic_motor(self, goal, proprioception):
        mean, _ = self.motor.parameters_for(goal, proprioception)
        return torch.tanh(mean)
    def critic(self, hidden, proprioception): return torch.tensor(0.0)


class UnpacedTests(unittest.TestCase):
    def test_client_drives_canonical_continuous_physics(self):
        client = UnpacedHostClient("test-unpaced")
        try:
            self.assertIsInstance(client.runtime, ZoneRuntime)
            before = client.state()
            player = player_from_state(before)
            self.assertEqual(before["snapshot"]["physics_hz"], 120)
            self.assertEqual(float(player["x"]), 100.0)
            response = client.motor(1.0)
            client.advance_tick()
            after = player_from_state(client.state())
            self.assertEqual(after["last_sequence"], response["sequence"])
            self.assertGreater(float(after["vx"]), 0.0)
            self.assertGreater(float(after["x"]), 100.0)
            self.assertLess(float(after["vx"]), 180.0)
        finally:
            client.close()

    def test_unpaced_reset_accepts_explicit_spawn_position(self):
        client = UnpacedHostClient("test-unpaced-reset")
        try:
            state = reset_player_state(client, "player1", spawn_x=640.0)
            player = player_from_state(state)
            self.assertEqual(float(player["x"]), 640.0)
            self.assertEqual(float(player["vx"]), 0.0)
            self.assertEqual(float(player["motor_x"]), 0.0)
        finally:
            client.close()

    def test_control_can_physically_settle_on_987(self):
        client = UnpacedHostClient("test-unpaced-control")
        transitions = []
        try:
            ensure_player(client, "player1")
            state = reset_player_state(client, "player1")
            result = control_loop(
                RuleModel(),
                client,
                state,
                target_x=987.0,
                tolerance=0.9,
                max_seconds=8.0,
                on_transition=transitions.append,
            )
        finally:
            client.close()

        self.assertEqual(result["execution_mode"], "unpaced")
        self.assertEqual(result["status"], "reached")
        self.assertLessEqual(abs(result["error"]), 0.9)
        self.assertEqual(result["vx"], 0.0)
        self.assertGreaterEqual(result["stable_ticks"], 12)
        self.assertTrue(transitions)
        self.assertTrue(all(item.next_tick > item.tick for item in transitions))
        self.assertAlmostEqual(result["effective_motor_hz"], 60.0, delta=10.0)


if __name__ == "__main__":
    unittest.main()
