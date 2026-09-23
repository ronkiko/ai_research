from __future__ import annotations

import unittest

import torch

from gamelab.control import control_loop
from gamelab.host import player_from_state
from gamelab.runtime import ensure_player, reset_player_state
from gamelab.unpaced import UnpacedHostClient
from gameserver.v1.zone.model import ZoneRuntime


class RuleModel:
    """Deterministic test policy; proves executor/world symmetry, not learning."""

    def eval(self):
        return None

    def spine(self, history):
        goal_dx = history[3, -1]
        zeros = torch.zeros(3, dtype=goal_dx.dtype)
        return torch.cat((goal_dx.reshape(1), zeros)), torch.zeros(16)

    def motor(self, goal, proprioception):
        dx = float(goal[0])
        if dx > 0.002:
            return torch.tensor([-100.0, -100.0, 100.0])
        if dx < -0.002:
            return torch.tensor([100.0, -100.0, -100.0])
        return torch.tensor([-100.0, 100.0, -100.0])

    def critic(self, hidden, proprioception):
        return torch.tensor(0.0)

    def action_to_move(self, action):
        return (-1, 0, 1)[action]


class UnpacedTests(unittest.TestCase):
    def test_client_drives_the_canonical_zone_runtime(self):
        client = UnpacedHostClient("test-unpaced")
        try:
            self.assertIsInstance(client.runtime, ZoneRuntime)
            before = client.state()
            player = player_from_state(before)
            self.assertEqual(before["snapshot"]["physics_hz"], 120)
            self.assertEqual(float(player["x"]), 100.0)

            response = client.input(1)
            queued = client.state()
            self.assertEqual(player_from_state(queued)["last_sequence"], 0)
            client.advance_tick()
            applied = client.state()
            after = player_from_state(applied)
            self.assertEqual(after["last_sequence"], response["sequence"])
            self.assertEqual(
                after["last_input_command_id"],
                response["event"]["command_id"],
            )
            self.assertAlmostEqual(float(after["x"]), 101.5)
        finally:
            client.close()

    def test_reset_and_control_use_virtual_ticks_without_wall_clock_semantics(self):
        client = UnpacedHostClient("test-unpaced-control")
        transitions = []
        try:
            ensure_player(client, "player1")
            state = reset_player_state(client, "player1")
            result = control_loop(
                RuleModel(),
                client,
                state,
                target_x=110.0,
                tolerance=1.0,
                max_seconds=2.0,
                on_transition=transitions.append,
            )
        finally:
            client.close()

        self.assertEqual(result["execution_mode"], "unpaced")
        self.assertEqual(result["status"], "reached")
        self.assertLessEqual(abs(result["error"]), 1.0)
        self.assertEqual(result["vx"], 0.0)
        self.assertEqual(result["move_x"], 0)
        self.assertGreaterEqual(result["stable_ticks"], 12)
        self.assertTrue(transitions)
        self.assertTrue(all(item.next_tick > item.tick for item in transitions))
        self.assertTrue(all(item.next_tick - item.tick == 2 for item in transitions))
        self.assertTrue(all(item.elapsed_steps == 1.0 for item in transitions))
        self.assertAlmostEqual(result["effective_motor_hz"], 60.0, delta=10.0)
        self.assertGreater(result["simulation_seconds"], 0.0)
        self.assertGreaterEqual(result["speedup"], 0.0)


if __name__ == "__main__":
    unittest.main()
