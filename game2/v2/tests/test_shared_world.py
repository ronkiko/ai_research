from __future__ import annotations

import unittest
from pathlib import Path

from game2.v2.console.display.view_state import DisplayState
from game2.v2.console.display.vision.renderer import VisionClass, VisionRenderer
from game2.v2.console.engine.engine import Engine, PlayerRegistry, TERMINAL
from game2.v2.console.protocol import InputStateCommand
from game2.v2.console.world import load_world


ROOT = Path(__file__).resolve().parents[3]
PIT = ROOT / "game2" / "v2" / "console" / "world" / "maps" / "pit.json"


class SharedWorldRuntimeTests(unittest.TestCase):
    def test_engine_starts_with_zero_actors_and_global_clock(self):
        engine = Engine(load_world(PIT))
        self.assertEqual(engine.actors, {})
        self.assertEqual(engine.world_tick, 0)
        for expected in range(1, 4):
            engine.tick()
            self.assertEqual(engine.world_tick, expected)
        self.assertEqual(engine.actors, {})

    def test_player_registry_keeps_distinct_player_and_actor_ids(self):
        registry = PlayerRegistry()
        binding = registry.register("player-A", "actor-A")
        self.assertEqual((binding.player_id, binding.actor_id), ("player-A", "actor-A"))
        self.assertEqual(registry.lookup("player-A"), binding)
        with self.assertRaises(ValueError):
            registry.register("actor-A", "actor-A")

    def test_two_actors_share_one_clock_and_neutral_input(self):
        engine = Engine(load_world(PIT))
        engine.spawn_actor("player-A", "actor-A")
        engine.spawn_actor("player-B", "actor-B")
        self.assertEqual(set(engine.actors), {"actor-A", "actor-B"})
        self.assertEqual(
            engine.submit_input(InputStateCommand("actor-A", 1, True, False)),
            "accepted",
        )
        engine.tick()
        self.assertGreater(engine.actors["actor-A"].body.vx, 0)
        self.assertEqual(engine.actors["actor-B"].body.vx, 0)
        self.assertEqual(engine.world_tick, 1)

    def test_input_latches_and_sequences_are_actor_scoped(self):
        engine = Engine(load_world(PIT))
        engine.spawn_actor("player-A", "actor-A")
        engine.spawn_actor("player-B", "actor-B")
        self.assertEqual(
            engine.submit_input(InputStateCommand("actor-A", 1, True, False)),
            "accepted",
        )
        self.assertEqual(
            engine.submit_input(InputStateCommand("actor-B", 1, False, False)),
            "accepted",
        )
        self.assertEqual(
            engine.submit_input(InputStateCommand("unknown", 1, True, False)),
            "rejected",
        )
        self.assertEqual(
            engine.submit_input(InputStateCommand("actor-A", 1, False, False)),
            "duplicate",
        )
        for _ in range(4):
            engine.tick()
        self.assertGreater(engine.actors["actor-A"].body.vx, 0.0)
        self.assertEqual(engine.actors["actor-B"].body.vx, 0.0)

    def test_terminal_actor_does_not_freeze_other_actor_or_world(self):
        engine = Engine(load_world(PIT))
        engine.spawn_actor("player-A", "actor-A")
        engine.spawn_actor("player-B", "actor-B")
        actor_a = engine.actors["actor-A"]
        actor_b = engine.actors["actor-B"]
        actor_a.body.x, actor_a.body.y, actor_a.body.vy = 512, 500, 30_000
        actor_a.body.grounded = False
        engine.submit_input(InputStateCommand("actor-B", 1, True, False))
        engine.tick()
        self.assertEqual(actor_a.result, "dead")
        self.assertEqual(actor_a.lifecycle, TERMINAL)
        self.assertFalse(actor_a.input_right)
        self.assertFalse(actor_a.input_jump)
        x_after_death = actor_b.body.x
        engine.tick()
        self.assertEqual(actor_a.result, "dead")
        self.assertGreater(actor_b.body.x, x_after_death)
        self.assertEqual(engine.world_tick, 2)

    def test_respawn_is_actor_local(self):
        engine = Engine(load_world(PIT))
        engine.spawn_actor("player-A", "actor-A")
        engine.spawn_actor("player-B", "actor-B")
        actor_a, actor_b = engine.actors["actor-A"], engine.actors["actor-B"]
        actor_a.result, actor_a.lifecycle = "dead", TERMINAL
        actor_a.body.x = engine.world.spawn.x + 77
        actor_b.body.x = engine.world.spawn.x + 123
        before = engine.world_tick
        engine.respawn_actor("actor-A")
        self.assertEqual(engine.world_tick, before)
        self.assertEqual((actor_a.body.x, actor_a.body.y),
                         (engine.world.spawn.x, engine.world.spawn.y))
        self.assertIsNone(actor_a.result)
        self.assertFalse(actor_a.input_right)
        self.assertFalse(actor_a.input_jump)
        self.assertEqual(actor_b.body.x, engine.world.spawn.x + 123)
        self.assertEqual(actor_b.result, None)

    def test_despawn_is_actor_local(self):
        engine = Engine(load_world(PIT))
        engine.spawn_actor("player-A", "actor-A")
        engine.spawn_actor("player-B", "actor-B")
        before = engine.world_tick
        world = engine.world
        engine.despawn_actor("actor-A")
        self.assertNotIn("actor-A", engine.actors)
        self.assertIn("actor-B", engine.actors)
        self.assertIs(engine.world, world)
        self.assertEqual(engine.world_tick, before)

    def test_state_is_multi_actor_without_singleton_fields(self):
        engine = Engine(load_world(PIT))
        engine.spawn_actor("player-A", "actor-A")
        engine.spawn_actor("player-B", "actor-B")
        payload = engine.world_state().to_payload()
        self.assertEqual([actor["actor_id"] for actor in payload["actors"]],
                         ["actor-A", "actor-B"])
        for field in ("avatar", "terminal", "episode", "episode_tick"):
            self.assertNotIn(field, payload)

    def test_multi_perspective_vision_marks_self_and_other_actor(self):
        world = load_world(PIT)
        engine = Engine(world)
        engine.spawn_actor("player-A", "actor-A")
        engine.spawn_actor("player-B", "actor-B")
        engine.actors["actor-B"].body.x = world.spawn.x + 128
        state = DisplayState.from_state(engine.world_state(), engine.session_id, world,
                                        "actor-A")
        renderer = VisionRenderer(world)
        frame_a = renderer.render(state)
        frame_b = renderer.render(state, self_actor_id="actor-B")
        a_pixel = int(world.spawn.x + world.spawn.width // 2)
        b_pixel = int(world.spawn.x + 128 + world.spawn.width // 2)
        row = int(world.spawn.y + world.spawn.height // 2) * world.width
        self.assertEqual(frame_a.pixels[row + a_pixel], VisionClass.SELF)
        self.assertEqual(frame_a.pixels[row + b_pixel], VisionClass.OTHER_ACTOR)
        self.assertEqual(frame_b.pixels[row + a_pixel], VisionClass.OTHER_ACTOR)
        self.assertEqual(frame_b.pixels[row + b_pixel], VisionClass.SELF)


if __name__ == "__main__":
    unittest.main()
