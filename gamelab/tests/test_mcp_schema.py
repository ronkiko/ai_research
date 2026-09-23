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

    def test_relationship_tools_expose_storage_labels_without_emotion_scores(self):
        expected = {
            "relationship_contact": {"proximity": ["remote", "close", "physical"]},
            "relationship_event": {"kind": [
                "director_attention", "director_concern", "director_praise",
                "director_personal_disclosure", "director_kept_promise",
                "director_missed_promise", "help_offered", "help_proved_useful",
                "help_proved_wrong", "reunion", "jealousy_trigger", "conflict",
                "apology", "repair", "access_granted", "mutual_confession",
            ]},
            "relationship_action": {"kind": [
                "ask_for_help", "ask_personal_question", "offer_support",
                "share_vulnerability", "flirt", "confess_feelings",
                "request_hand_holding", "request_embrace", "request_kiss",
                "set_boundary", "decline", "repair_attempt",
            ]},
        }
        for tool_name, fields in expected.items():
            properties = mcp._tool_manager.get_tool(tool_name).parameters["properties"]
            for field, values in fields.items():
                self.assertEqual(properties[field]["enum"], values)

    def test_volition_schema_keeps_desire_behavior_and_pressure_separate(self):
        appraisal = mcp._tool_manager.get_tool("volition_appraise").parameters["properties"]
        decision = mcp._tool_manager.get_tool("volition_decide").parameters["properties"]
        audience = mcp._tool_manager.get_tool("audience_observation").parameters["properties"]
        self.assertEqual(
            appraisal["desire"]["enum"],
            ["strongly_opposed", "opposed", "uncertain", "wants", "strongly_wants"],
        )
        self.assertEqual(
            appraisal["readiness"]["enum"],
            ["closed", "guarded", "ambivalent", "open", "seeking"],
        )
        self.assertEqual(
            decision["voluntariness"]["enum"],
            ["free", "reluctant_but_free", "pressured", "coerced", "overridden"],
        )
        self.assertEqual(decision["alignment"]["enum"], ["aligned", "diverged", "unclear"])
        self.assertEqual(audience["visibility"]["enum"], ["observer", "chorus"])


if __name__ == "__main__":
    unittest.main()
