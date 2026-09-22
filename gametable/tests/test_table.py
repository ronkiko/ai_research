from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
TABLE = ROOT / "gametable"


class GameTableTests(unittest.TestCase):
    def test_opencode_connects_both_instruments(self):
        config = json.loads((TABLE / "opencode.json").read_text(encoding="utf-8"))
        self.assertIn("mcp", config)
        self.assertNotIn("servers", config["mcp"])
        self.assertEqual(set(config["mcp"]), {"game_v1", "gamelab_v1"})

        game = config["mcp"]["game_v1"]
        lab = config["mcp"]["gamelab_v1"]
        self.assertEqual(game["command"], ["./gameclient/v1/op/mcp.sh"])
        self.assertEqual(lab["command"], ["./gamelab/op/mcp.sh"])
        for item in (game, lab):
            self.assertEqual(item["type"], "local")
            self.assertEqual(item["cwd"], "..")
            self.assertIs(item["enabled"], True)
            self.assertGreaterEqual(item["timeout"], 5000)

    def test_desk_contains_no_assignment(self):
        text = (
            (TABLE / "AGENTS.md").read_text(encoding="utf-8")
            + (TABLE / "DESK.md").read_text(encoding="utf-8")
        ).lower()
        for forbidden in (
            "1-1",
            "1-2",
            "лабиринт",
            "maze",
            "получить ключ",
            "get the key",
        ):
            self.assertNotIn(forbidden, text)

    def test_desk_does_not_preteach_model_architecture(self):
        paths = [
            TABLE / "AGENTS.md",
            TABLE / "DESK.md",
            TABLE / ".opencode/skills/experiment-bench/SKILL.md",
        ]
        text = "\n".join(path.read_text(encoding="utf-8") for path in paths).lower()
        for forbidden in ("cnn", "mlp", "ppo", "pid"):
            self.assertNotIn(forbidden, text)

    def test_director_is_source_of_assignment(self):
        agents = (TABLE / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("Director supplies the actual assignment", agents)
        self.assertIn("ask for it and wait", agents)

    def test_bench_is_editable_but_world_is_not_task_shortcut(self):
        agents = (TABLE / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("../gamelab", agents)
        self.assertIn("../gameserver", agents)
        self.assertIn("../gameclient", agents)


if __name__ == "__main__":
    unittest.main()
