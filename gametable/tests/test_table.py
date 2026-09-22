from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
TABLE = ROOT / "gametable"
SKILLS = TABLE / ".opencode" / "skills"
SKILL_001 = "001-игровой_клиент_и_базовая_информация_об_игре"
SKILL_002 = "002-игровая_лаборатория_по_изучению_игровых_механик"
SKILL_003_ADV = "003-лаборатория_расширеные_настройки"
SKILL_003_PLUGINS = "003-лаборатория_плагины_подключаем_и_пишем_свои"


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

    def test_expected_manuals_are_on_the_table(self):
        manuals = {
            path.parent.name
            for path in SKILLS.glob("*/SKILL.md")
        }
        self.assertEqual(
            manuals,
            {SKILL_001, SKILL_002, SKILL_003_ADV, SKILL_003_PLUGINS},
        )

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
            SKILLS / SKILL_001 / "SKILL.md",
            SKILLS / SKILL_002 / "SKILL.md",
            SKILLS / SKILL_003_ADV / "SKILL.md",
            SKILLS / SKILL_003_PLUGINS / "SKILL.md",
        ]
        text = "\n".join(path.read_text(encoding="utf-8") for path in paths).lower()
        for forbidden in ("cnn", "mlp", "ppo", "pid"):
            self.assertIsNone(
                re.search(rf"\b{re.escape(forbidden)}\b", text),
                forbidden,
            )

    def test_assistant_uses_mcp_not_neighbor_scripts(self):
        paths = [
            TABLE / "AGENTS.md",
            TABLE / "DESK.md",
            SKILLS / SKILL_002 / "SKILL.md",
            SKILLS / SKILL_003_ADV / "SKILL.md",
            SKILLS / SKILL_003_PLUGINS / "SKILL.md",
        ]
        text = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        for forbidden in (
            "../gamelab/op/",
            "../gamelab/",
            "../gameclient/",
            "../gameserver/",
        ):
            self.assertNotIn(forbidden, text)
        self.assertIn("MCP", text)

    def test_second_manual_explains_shared_game_and_full_lab_surface(self):
        text = (SKILLS / SKILL_002 / "SKILL.md").read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        self.assertIn("несколько джойстиков", normalized)
        self.assertIn("одной активной игровой сессией", normalized)
        self.assertIn("умеет сама выполнить `login`", normalized)
        self.assertIn("не выполняет `logout`", normalized)
        self.assertIn("общую монотонную sequence", normalized)
        self.assertIn("неразрушающий reset", normalized)
        self.assertIn("session и Host sequence сохраняются", normalized)
        self.assertIn("RUN reset не выполняет", normalized)
        for tool in (
            "gamelab_v1_health",
            "gamelab_v1_login",
            "gamelab_v1_describe",
            "gamelab_v1_reward_get",
            "gamelab_v1_reward_set",
            "gamelab_v1_training_start",
            "gamelab_v1_training_status",
            "gamelab_v1_training_cancel",
            "gamelab_v1_verify_start",
            "gamelab_v1_verify_status",
            "gamelab_v1_verify_cancel",
            "gamelab_v1_run_start",
            "gamelab_v1_run_status",
            "gamelab_v1_run_cancel",
            "gamelab_v1_run_update_goal",
            "gamelab_v1_executive_begin",
            "gamelab_v1_executive_state",
            "gamelab_v1_executive_strategy_begin",
            "gamelab_v1_executive_strategy_end",
            "gamelab_v1_executive_director_signal",
            "gamelab_v1_executive_question",
            "gamelab_v1_executive_finish",
            "gamelab_v1_relationship_state",
            "gamelab_v1_relationship_event",
            "gamelab_v1_relationship_action",
            "gamelab_v1_relationship_consent",
            "gamelab_v1_relationship_employment_decision",
        ):
            self.assertIn(tool, text)

    def test_second_manual_documents_executive_without_autopilot(self):
        text = (SKILLS / SKILL_002 / "SKILL.md").read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        for expected in (
            "180 минут",
            "current и best result раздельно",
            "PLATEAU",
            "STRATEGY_RELAPSE",
            "не двигает персонажа",
            "не запускает и не отменяет эксперименты",
        ):
            self.assertIn(expected, normalized)

    def test_advanced_manual_documents_host_instances(self):
        text = (SKILLS / SKILL_003_ADV / "SKILL.md").read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        for expected in (
            "game-v1-default",
            "17700",
            "17701",
            "gamelab_v1_host_list",
            "gamelab_v1_host_create",
            "gamelab_v1_host_delete",
            "PERMISSION_DENIED",
            "host_id",
        ):
            self.assertIn(expected, normalized)

    def test_plugins_manual_is_explicit_placeholder(self):
        text = (
            SKILLS / SKILL_003_PLUGINS / "SKILL.md"
        ).read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        self.assertIn("пока не реализована", normalized)
        self.assertIn("feedback@gamelab", normalized)

    def test_director_is_source_of_assignment(self):
        agents = (TABLE / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("Director supplies the actual assignment", agents)
        self.assertIn("ask for it and wait", agents)

    def test_yuki_profile_is_mandatory_and_does_not_change_science(self):
        agents = (TABLE / "AGENTS.md").read_text(encoding="utf-8")
        profile = (TABLE / "characters" / "002-yuki.md").read_text(encoding="utf-8")
        self.assertIn("002-yuki.md", agents)
        self.assertIn("совершеннолетняя", profile)
        self.assertIn("не заменяет машинное evidence", profile)
        self.assertIn("не являются согласием", profile)


if __name__ == "__main__":
    unittest.main()
