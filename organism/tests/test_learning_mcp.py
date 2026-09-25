from __future__ import annotations

import unittest

from organism.mcp import mcp


EXPECTED_TOOLS = {
    "describe",
    "skills",
    "training_prepare",
    "motor_train_start",
    "spine_train_start",
    "training_status",
    "training_cancel",
    "verify_start",
    "verify_status",
    "verify_cancel",
    "skill_select",
}


class LearningMcpSchemaTests(unittest.TestCase):
    def test_expected_learning_v1_surface_exists(self):
        names = set(mcp._tool_manager._tools)
        self.assertTrue(EXPECTED_TOOLS <= names)

    def test_training_tools_are_id_based_and_bounded(self):
        forbidden = {
            "path", "file", "directory", "checkpoint_path", "load_path",
            "save_path", "python", "code", "expression", "entity_id",
            "embodiment_id", "x", "motor_x",
        }
        for name in EXPECTED_TOOLS:
            schema = mcp._tool_manager.get_tool(name).parameters
            self.assertFalse(forbidden & set(schema.get("properties", {})), name)

        motor = mcp._tool_manager.get_tool(
            "motor_train_start"
        ).parameters["properties"]
        spine = mcp._tool_manager.get_tool(
            "spine_train_start"
        ).parameters["properties"]
        self.assertEqual(motor["budget"]["minimum"], 1)
        self.assertEqual(motor["budget"]["maximum"], 500)
        self.assertEqual(spine["budget"]["minimum"], 1)
        self.assertEqual(spine["budget"]["maximum"], 500)

    def test_prepare_has_authorization_reference_but_no_authority_minting_tool(self):
        prepare = mcp._tool_manager.get_tool(
            "training_prepare"
        ).parameters["properties"]
        self.assertIn("authorization_id", prepare)
        self.assertFalse(
            any("authorize" in name or "grant" in name
                for name in mcp._tool_manager._tools)
        )


if __name__ == "__main__":
    unittest.main()
