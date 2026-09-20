from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from game2.v2.contracts.bot_profile import BotProfile
from game2.v2.management.bot_profiles import BotProfileStore


def _profile(bot_id: str = "player1") -> BotProfile:
    return BotProfile.from_dict({
        "schema_version": 1,
        "bot_id": bot_id,
        "display_name": "Player 1",
        "cerebral_cortex": {
            "enabled": False,
            "role": "research_strategist",
            "implementation": "none",
            "configuration": "disabled",
            "precision": "n/a",
            "seed": None,
            "topology": {
                "kind": "none", "inputs": None, "hidden": [],
                "outputs": None, "summary": "disabled",
            },
        },
        "spinal_cord": {
            "enabled": True,
            "role": "planner",
            "implementation": "cnn",
            "configuration": "shared-pool4-plan-context-v9",
            "precision": "fp32",
            "seed": 1,
            "topology": {
                "kind": "cnn", "inputs": None, "hidden": [16],
                "outputs": 7, "summary": "planner",
            },
        },
        "motors": [
            {
                "motor_id": "right", "enabled": True, "role": "motor",
                "implementation": "mlp",
                "configuration": "button-reflex-5-8-3-v1",
                "precision": "fp32", "seed": 2,
                "topology": {
                    "kind": "mlp", "inputs": 5, "hidden": [8],
                    "outputs": 3, "summary": "right reflex",
                },
            },
            {
                "motor_id": "jump", "enabled": True, "role": "motor",
                "implementation": "mlp",
                "configuration": "button-reflex-5-8-3-v1",
                "precision": "fp32", "seed": 3,
                "topology": {
                    "kind": "mlp", "inputs": 5, "hidden": [8],
                    "outputs": 3, "summary": "jump reflex",
                },
            },
        ],
    })


class BotProfileTests(unittest.TestCase):
    def test_roundtrip_and_console_identity(self):
        profile = _profile()
        restored = BotProfile.from_json(profile.to_json())
        self.assertEqual(restored, profile)
        self.assertEqual(restored.player_id, "player1")
        self.assertEqual(
            [motor.motor_id for motor in restored.motors],
            ["right", "jump"],
        )

    def test_contract_rejects_duplicate_motor_ids_and_unknown_fields(self):
        data = _profile().to_dict()
        data["motors"][1]["motor_id"] = "right"
        with self.assertRaises(ValueError):
            BotProfile.from_dict(data)

        data = _profile().to_dict()
        data["surprise"] = True
        with self.assertRaises(ValueError):
            BotProfile.from_dict(data)

        data = _profile().to_dict()
        data["schema_version"] = True
        with self.assertRaises(ValueError):
            BotProfile.from_dict(data)

    def test_contract_rejects_duplicate_json_fields(self):
        text = _profile().to_json()
        data = json.loads(text)
        prefix = '{"schema_version":1,"schema_version":1,'
        body = json.dumps({key: value for key, value in data.items()
                           if key != "schema_version"})[1:]
        with self.assertRaises(ValueError):
            BotProfile.from_json(prefix + body)

    def test_store_lists_loads_and_atomically_replaces_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            store = BotProfileStore(directory)
            first = _profile("player1")
            path = store.save(first)
            self.assertEqual(path.name, "player1.json")
            self.assertEqual(store.list_ids(), ("player1",))
            self.assertEqual(store.load("player1"), first)

            changed = BotProfile.from_dict({
                **first.to_dict(),
                "display_name": "Updated Player",
            })
            store.save(changed)
            self.assertEqual(store.load("player1").display_name, "Updated Player")
            self.assertFalse(Path(str(path) + ".tmp").exists())

    def test_store_rejects_unknown_or_unsafe_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            store = BotProfileStore(directory)
            with self.assertRaises(ValueError):
                store.load("../player1")
            with self.assertRaises(ValueError):
                store.load("missing")
            with self.assertRaises(ValueError):
                store.save(_profile("catalog"))


if __name__ == "__main__":
    unittest.main()
