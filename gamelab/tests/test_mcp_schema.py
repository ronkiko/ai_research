from __future__ import annotations

import unittest

from gamelab.mcp import mcp


class McpSchemaTests(unittest.TestCase):
    def test_director_signal_exposes_allowed_kinds(self):
        schema = mcp._tool_manager.get_tool("executive_director_signal").parameters
        self.assertEqual(
            schema["properties"]["kind"]["enum"],
            [
                "constraint",
                "correction",
                "information",
                "offer_help",
                "deadline",
                "praise",
                "pressure",
            ],
        )

    def test_reward_set_exposes_runtime_bounds(self):
        schema = mcp._tool_manager.get_tool("reward_set").parameters
        properties = schema["properties"]
        expected = {
            "distance_progress_scale": (0.0, 20.0),
            "step_cost": (0.0, 1.0),
            "success_bonus": (0.0, 20.0),
            "timeout_penalty": (0.0, 20.0),
            "stopped_near_goal_bonus": (-20.0, 20.0),
            "near_goal_radius": (0.1, 250.0),
        }
        for name, (minimum, maximum) in expected.items():
            numeric = properties[name]["anyOf"][0]
            self.assertEqual(numeric["minimum"], minimum)
            self.assertEqual(numeric["maximum"], maximum)


if __name__ == "__main__":
    unittest.main()
