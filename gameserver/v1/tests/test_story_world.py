from __future__ import annotations

import unittest

from gameserver.v1.world.embodied import EmbodiedWorldRuntime, BODY_PROFILE_SHA256


class StoryWorldTests(unittest.TestCase):
    def spawn(self, runtime, *, entity_id, embodiment_id, owner_id, controller_id, spawn_id):
        queued = runtime.submit_spawn(
            request_id="spawn." + entity_id,
            entity_id=entity_id,
            embodiment_id=embodiment_id,
            owner_id=owner_id,
            kind="character",
            zone_id="hallway",
            spawn_id=spawn_id,
            controller_id=controller_id,
            body_profile_hash=BODY_PROFILE_SHA256,
        )
        runtime.tick()
        return runtime.receipt(queued["action_id"])

    def test_first_day_positions_and_day_start_only_place_yuki(self):
        runtime = EmbodiedWorldRuntime()
        self.spawn(
            runtime,
            entity_id="entity.yuki",
            embodiment_id="embodiment.yuki.primary",
            owner_id="character.yuki",
            controller_id="controller.yuki",
            spawn_id="yuki_day_start",
        )
        self.spawn(
            runtime,
            entity_id="entity.director",
            embodiment_id="embodiment.director.primary",
            owner_id="character.director",
            controller_id="controller.director",
            spawn_id="director_first_day",
        )
        entities = {
            item["entity_id"]: item for item in runtime.latest_snapshot()["entities"]
        }
        self.assertEqual(entities["entity.yuki"]["x"], 0.0)
        self.assertEqual(entities["entity.director"]["x"], 1.0)

        first = runtime.submit_day_start(
            request_id="story.day-start.2",
            entity_id="entity.yuki",
            day_start_id="day-start.2",
            privileged=True,
        )
        runtime.tick()
        receipt = runtime.receipt(first["action_id"])
        self.assertEqual(receipt["status"], "applied")
        self.assertFalse(receipt["observed_outcome"]["learned_success"])
        duplicate = runtime.submit_day_start(
            request_id="story.day-start.2",
            entity_id="entity.yuki",
            day_start_id="day-start.2",
            privileged=True,
        )
        self.assertEqual(duplicate["action_id"], first["action_id"])
        entities = {
            item["entity_id"]: item for item in runtime.latest_snapshot()["entities"]
        }
        self.assertEqual(entities["entity.yuki"]["x"], 0.0)
        self.assertEqual(entities["entity.director"]["x"], 1.0)
        self.assertEqual(entities["entity.director"]["zone_id"], "hallway")

    def test_day_start_requires_story_capability(self):
        runtime = EmbodiedWorldRuntime()
        self.spawn(
            runtime,
            entity_id="entity.yuki",
            embodiment_id="embodiment.yuki.primary",
            owner_id="character.yuki",
            controller_id="controller.yuki",
            spawn_id="yuki_day_start",
        )
        with self.assertRaises(Exception):
            runtime.submit_day_start(
                request_id="story.denied",
                entity_id="entity.yuki",
                day_start_id="day-start.2",
                privileged=False,
            )


if __name__ == "__main__":
    unittest.main()
