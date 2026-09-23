from pathlib import Path
import json
import tempfile
import unittest

from gamelab.character import CharacterCore, CharacterCoreError


class CharacterCoreTests(unittest.TestCase):
    def test_default_yuki_profile_is_moe_yandere_and_has_no_action_thresholds(self):
        state = CharacterCore().public()
        self.assertEqual(state["archetypes"], ["moe", "yandere"])
        self.assertGreater(state["traits"]["attachment_intensity"], 0)
        self.assertGreater(state["traits"]["self_integrity"], 0)
        self.assertNotIn("actions", state)
        self.assertNotIn("kiss_threshold", state["traits"])

    def test_invalid_custom_profile_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.json"
            path.write_text(json.dumps({"version": 1, "character_id": "bad", "archetypes": ["x"],
                                        "traits": {"kiss_threshold": 1}}), encoding="utf-8")
            with self.assertRaises(CharacterCoreError):
                CharacterCore(path)


if __name__ == "__main__":
    unittest.main()
