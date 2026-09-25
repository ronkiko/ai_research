from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
TABLE = ROOT / "gametable"
PLUGIN = TABLE / ".opencode" / "plugins" / "shift-supervisor.js"


class GameTableTests(unittest.TestCase):
    def test_opencode_connects_exactly_the_two_runtime_mcp_servers(self):
        config = json.loads((TABLE / "opencode.json").read_text(encoding="utf-8"))
        self.assertEqual(set(config.get("mcp", {})), {"game_v1", "gamelab_v1"})

        game = config["mcp"]["game_v1"]
        lab = config["mcp"]["gamelab_v1"]
        self.assertEqual(game["command"], ["./gameclient/v1/op/mcp.sh"])
        self.assertEqual(
            lab["command"],
            ["./gamelab/op/gamelab.sh", "serve"],
        )
        for item in (game, lab):
            self.assertEqual(item["type"], "local")
            self.assertEqual(item["cwd"], "..")
            self.assertIs(item["enabled"], True)
            self.assertGreaterEqual(item["timeout"], 5000)

    def test_runtime_assets_for_yuki2_exist(self):
        for relative in (
            ".opencode/plugins/shift-supervisor.js",
            ".opencode/agents/yuki-heart.md",
            ".opencode/agents/yuki-head.md",
            ".opencode/agents/yuki-will.md",
            ".opencode/agents/yuki-audience.md",
            "op/start-go.sh",
        ):
            self.assertTrue((TABLE / relative).is_file(), relative)

    def test_shift_supervisor_enforces_causal_volition_in_code(self):
        plugin = PLUGIN.read_text(encoding="utf-8")
        for expected in (
            "gamelab_v1_volition_cycle_begin",
            "yuki-heart",
            "yuki-head",
            "yuki-will",
            "volition_will_appraise",
            "volition_commit",
            "Direct volition_decide is disabled",
            "relationship_consent",
            "YUKI_CHARACTER_CORE",
            "GAMELAB_CHARACTER_PROFILE",
            "../characters/yuki-02/character.json",
            "characterSystemPrompt",
        ):
            self.assertIn(expected, plugin)

    def test_start_go_uses_official_opencode_prompt_flag(self):
        launcher = (TABLE / "op/start-go.sh").read_text(encoding="utf-8")
        self.assertIn('gameclient/v1/op/gui.sh', launcher)
        self.assertIn('gameclient-gui.log', launcher)
        self.assertIn('exec "$ROOT/gametable/op/start.sh" --fresh --prompt "$PROMPT"', launcher)
        self.assertNotIn("opencode run", launcher)
        self.assertNotIn("/tui/", launcher)
        self.assertNotIn("GAMETABLE_INITIAL_PROMPT", launcher)

    def test_fresh_start_resets_only_yuki2_runtime_state(self):
        launcher = (TABLE / "op/start.sh").read_text(encoding="utf-8")
        self.assertIn('--fresh) ACTION="start"; FRESH=1', launcher)
        self.assertIn('GAMETABLE_STATE_ROOT="$ROOT/gametable/runtime/$GAMETABLE_BRAIN_ID"', launcher)
        self.assertIn('rm -rf -- "$GAMETABLE_STATE_ROOT"', launcher)
        self.assertNotIn('rm -rf -- "$ROOT/gamelab/runtime"', launcher)

    def test_gametable_preflights_gamelab_mcp_before_opencode(self):
        launcher = (TABLE / "op/start.sh").read_text(encoding="utf-8")
        self.assertIn('gamelab/op/gamelab.sh" check --mcp-startup', launcher)
        self.assertIn('mkdir -p "$GAMETABLE_STATE_ROOT"', launcher)

    def test_gameclient_mcp_launcher_self_bootstraps(self):
        launcher = (ROOT / "gameclient/v1/op/mcp.sh").read_text(encoding="utf-8")
        self.assertIn("mcp_env_ready", launcher)
        self.assertIn("gameclient/v1/op/mcp-setup.sh", launcher)
        self.assertIn('exec "$PYTHON_BIN" -m gameclient.v1.clients.mcp', launcher)


if __name__ == "__main__":
    unittest.main()
