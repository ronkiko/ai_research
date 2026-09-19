from __future__ import annotations

import os
import socket
import threading
import time
import unittest
from dataclasses import FrozenInstanceError, fields
from typing import Any

from game2.v2.console.config import DisplayManifest
from game2.v2.console.display.display import DisplayService
from game2.v2.console.display.screen.autotile import AutoTiler, NeighborMask
from game2.v2.console.display.screen.renderer import ScreenRenderer, terminal_label
from game2.v2.console.display.screen.source import ScreenSourceService
from game2.v2.console.display.view_state import ActorView, DisplayState
from game2.v2.console.display.vision.renderer import VisionGridRenderer
from game2.v2.contracts.vision import META_GOAL, META_SELF
from game2.v2.console.world import Rect, TileID, WorldDefinition, load_world
from game2.v2.contracts.framing import encode_frame
from game2.v2.contracts.manifests import Endpoint

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
V2 = ROOT / "game2" / "v2"
PIT = V2 / "console" / "world" / "maps" / "pit.json"


def _tiny_world() -> WorldDefinition:
    tiles = (
        (TileID.EMPTY, TileID.SOLID, TileID.HAZARD, TileID.EMPTY),
        (TileID.EMPTY, TileID.EMPTY, TileID.EMPTY, TileID.EMPTY),
        (TileID.SOLID, TileID.SOLID, TileID.SOLID, TileID.SOLID),
    )
    return WorldDefinition(
        map_id="tiny", name="Tiny", tile_size=2, columns=4, rows=3,
        width=8, height=6, tiles=tiles,
        spawn=Rect(2, 2, 1, 1), goal=Rect(6, 0, 2, 2),
        collision_rects=(), decorations=(),
    )


def _view(world, x=2, y=2, world_tick=0, terminal=None, alive=True):
    actor = ActorView("actor-a", "player-a", x, y, 0, 0, True, alive, terminal)
    return DisplayState("session", world_tick, world.map_id, (actor,), "actor-a")


def _actor_payload(x: Any = 2, y: Any = 2, actor_id="actor-a", player_id="player-a", **extra):
    return {"actor_id": actor_id, "player_id": player_id, "x": x, "y": y,
            "vx": 0, "vy": 0, "grounded": True, "alive": True,
            "result": None, **extra}


class DisplayStateTests(unittest.TestCase):
    def test_terminal_values_are_validated_and_round_trip(self):
        world = _tiny_world()
        base = {
            "type": "state", "session_id": "session", "world_tick": 1,
            "map": "tiny", "actors": [_actor_payload()],
        }
        for terminal in (None, "success", "dead", "timeout"):
            with self.subTest(terminal=terminal):
                state = DisplayState.from_payload(
                    {**base, "actors": [_actor_payload(result=terminal)]}, "session", world)
                self.assertEqual(state.self_actor.result, terminal)
        with self.assertRaises(ValueError):
            DisplayState("session", 0, world.map_id,
                         (ActorView("actor-a", "player-a", 2, 2, 0, 0,
                                    True, True, "won"),), "actor-a")
        with self.assertRaises(ValueError):
            DisplayState.from_payload(
                {**base, "actors": [_actor_payload(result="won")]}, "session", world)


class AutoTilerTests(unittest.TestCase):
    def test_available_solid_variants_follow_cardinal_topology(self):
        empty, solid = TileID.EMPTY, TileID.SOLID
        isolated = ((empty, empty, empty), (empty, solid, empty), (empty, empty, empty))
        run = ((empty, empty, empty), (solid, solid, solid), (empty, empty, empty))
        vertical = ((solid, empty), (solid, empty), (empty, empty))
        enclosed = ((solid, solid, solid), (solid, solid, solid), (solid, solid, solid))
        autotiler = AutoTiler()
        self.assertEqual(autotiler.variant_for(isolated, 1, 1).atlas_cell, (0, 0))
        self.assertEqual(autotiler.variant_for(run, 1, 0).atlas_cell, (0, 0))
        self.assertEqual(autotiler.variant_for(run, 1, 1).atlas_cell, (1, 0))
        self.assertEqual(autotiler.variant_for(run, 1, 2).atlas_cell, (2, 0))
        self.assertEqual(autotiler.variant_for(vertical, 0, 0).atlas_cell, (0, 0))
        self.assertEqual(autotiler.variant_for(vertical, 1, 0).atlas_cell, (0, 1))
        self.assertEqual(autotiler.variant_for(enclosed, 1, 1).atlas_cell, (1, 1))
        self.assertEqual(autotiler.neighbor_mask(enclosed, 1, 1),
                         NeighborMask.NORTH | NeighborMask.EAST |
                         NeighborMask.SOUTH | NeighborMask.WEST)

    def test_map_border_and_hazard_fallback_are_deterministic(self):
        grid = ((TileID.SOLID, TileID.EMPTY), (TileID.EMPTY, TileID.HAZARD))
        autotiler = AutoTiler()
        first = autotiler.variant_for(grid, 0, 0)
        second = autotiler.variant_for(grid, 0, 0)
        hazard = autotiler.variant_for(grid, 1, 1)
        self.assertEqual(first, second)
        self.assertEqual(first.neighbor_mask, NeighborMask.NONE)
        self.assertIsNone(hazard.atlas_cell)
        self.assertEqual(hazard.tile, TileID.HAZARD)


class VisionGridRendererTests(unittest.TestCase):
    def test_logical_grid_keeps_physics_and_metadata_separate(self):
        world = _tiny_world()
        grid = VisionGridRenderer().render(world, _view(world))
        self.assertEqual(
            (grid.columns, grid.rows, grid.tile_size, grid.world_tick),
            (4, 3, 2, 0),
        )
        self.assertEqual(
            grid.physics,
            bytes((0, 1, 2, 0, 0, 0, 0, 0, 1, 1, 1, 1)),
        )
        self.assertEqual(grid.metadata[3], META_GOAL)
        self.assertEqual(grid.metadata[1 * grid.columns + 1], META_SELF)

    def test_self_and_goal_overlap_without_overwriting_each_other(self):
        world = _tiny_world()
        grid = VisionGridRenderer().render(
            world, _view(world, x=world.goal.x, y=world.goal.y)
        )
        goal_index = 3
        self.assertEqual(grid.physics[goal_index], 0)
        self.assertEqual(grid.metadata[goal_index], META_SELF | META_GOAL)

    def test_actor_aabb_marks_every_intersected_cell_and_keeps_physics(self):
        world = _tiny_world()
        grid = VisionGridRenderer().render(
            world,
            _view(world, x=3, y=2),
        )
        left = 1 * grid.columns + 1
        right = 1 * grid.columns + 2
        self.assertTrue(grid.metadata[left] & META_SELF)
        self.assertTrue(grid.metadata[right] & META_SELF)
        self.assertEqual(grid.physics[left], TileID.EMPTY)
        self.assertEqual(grid.physics[right], TileID.EMPTY)

    def test_public_vision_is_immutable_and_does_not_leak_physics_metadata(self):
        grid = VisionGridRenderer().render(_tiny_world(), _view(_tiny_world()))
        self.assertEqual(
            {field.name for field in fields(grid)},
            {"columns", "rows", "tile_size", "physics", "metadata", "world_tick"},
        )
        self.assertIs(type(grid.physics), bytes)
        self.assertIs(type(grid.metadata), bytes)
        for name in (
            "vx", "vy", "grounded", "accepted_inputs", "input_right",
            "input_jump", "collision_rects",
        ):
            self.assertFalse(hasattr(grid, name), name)
        with self.assertRaises(FrozenInstanceError):
            grid.metadata = b""


class DisplayServiceTests(unittest.TestCase):
    def _manifest(self, mode="vision"):
        return DisplayManifest("session", Endpoint("127.0.0.1", 1), "unused-map", mode)

    def test_vision_consumes_valid_state_and_ignores_invalid_payloads(self):
        world = _tiny_world()
        service = DisplayService(self._manifest(), world=world)
        valid = {
            "version": 1, "type": "state", "session_id": "session",
            "world_tick": 7, "map": "tiny", "actors": [_actor_payload()],
        }
        self.assertTrue(service.consume(valid))
        self.assertEqual(service.frames_received, 1)
        self.assertIsNotNone(service.latest_frame)
        for invalid in ({**valid, "type": "telemetry"}, {**valid, "session_id": "other"},
                        {**valid, "map": "other"},
                        {**valid, "actors": [_actor_payload(x="bad")]}):
            self.assertFalse(service.consume(invalid))
        self.assertEqual(service.frames_received, 1)
        service.renderer.close()

    def test_ingest_keeps_only_newest_monotonic_valid_state(self):
        world = _tiny_world()
        service = DisplayService(self._manifest(), world=world)
        valid = {
            "version": 1, "type": "state", "session_id": "session",
            "world_tick": 10, "map": "tiny", "actors": [_actor_payload()],
        }
        self.assertTrue(service.ingest(valid))
        self.assertFalse(service.ingest({**valid, "world_tick": 9}))
        self.assertFalse(service.ingest({**valid, "world_tick": 11,
                                         "actors": [_actor_payload(x="bad")] }))
        self.assertEqual(service.latest_state.world_tick, 10)
        self.assertEqual(service.frames_received, 1)
        self.assertEqual(service.rendered_frames, 0)
        service.renderer.close()

    def test_reader_continues_during_slow_screen_render_and_drops_backlog(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen()

        class SlowRenderer:
            def __init__(self):
                self.calls = []
                self.first_render_started = threading.Event()
                self.release = threading.Event()

            def render(self, view):
                self.calls.append(view.world_tick)
                if len(self.calls) == 1:
                    self.first_render_started.set()
                self.release.wait(2)
                return view

            def poll_close(self):
                return False

            def pace(self):
                return None

            def close(self):
                self.release.set()

        renderer = SlowRenderer()
        endpoint = Endpoint("127.0.0.1", listener.getsockname()[1])
        service = DisplayService(DisplayManifest("session", endpoint, "unused-map", "screen"),
                                 world=_tiny_world(), renderer=renderer)
        runner = threading.Thread(target=service.run, daemon=True)
        client = None
        try:
            runner.start()
            client, _ = listener.accept()
            client.sendall(encode_frame({
                "version": 1, "type": "state", "session_id": "session",
                "world_tick": 1, "map": "tiny", "actors": [_actor_payload()],
            }))
            self.assertTrue(renderer.first_render_started.wait(1))
            for tick in range(2, 6):
                client.sendall(encode_frame({
                    "version": 1, "type": "state", "session_id": "session",
                    "world_tick": tick, "map": "tiny",
                    "actors": [_actor_payload(x=2 + tick)],
                }))
            deadline = time.monotonic() + 1
            while service.frames_received < 5 and time.monotonic() < deadline:
                time.sleep(0.001)
            self.assertEqual(service.frames_received, 5)
            self.assertTrue(runner.is_alive())
            self.assertEqual(service.latest_state.world_tick, 5)
            self.assertEqual(renderer.calls, [1])
            renderer.release.set()
            client.close()
            client = None
            runner.join(2)
            self.assertFalse(runner.is_alive())
            self.assertEqual(renderer.calls, [1, 5])
            self.assertEqual(service.latest_frame.world_tick, 5)
        finally:
            renderer.release.set()
            if client is not None:
                client.close()
            runner.join(2)
            listener.close()


class ScreenTelemetryTests(unittest.TestCase):
    def test_hud_exposes_episode_budget_and_first_motion_delay(self):
        from game2.v2.console.config import ScreenSourceManifest

        world = _tiny_world()
        manifest = ScreenSourceManifest(
            "session",
            Endpoint("127.0.0.1", 1),
            "unused-map",
            Endpoint("127.0.0.1", 2),
            120,
            1200,
        )
        service = ScreenSourceService(manifest)
        service.world = world

        waiting = _view(world, x=world.spawn.x, y=world.spawn.y, world_tick=100)
        moved = DisplayState(
            "session",
            220,
            world.map_id,
            (
                ActorView(
                    "actor-a", "player-a",
                    world.spawn.x + 4, world.spawn.y,
                    1.0, 0.0, True, True, None,
                ),
            ),
            "actor-a",
        )
        with service.condition:
            service._track_episode(waiting)
            first_lines = service._hud_lines(waiting)
            service._track_episode(moved)
            moved_lines = service._hud_lines(moved)

        self.assertTrue(any("episode 0/1200" in line for line in first_lines))
        self.assertTrue(any("left 10.00s" in line for line in first_lines))
        self.assertTrue(any(
            "first move +120 ticks (1.00s)" in line for line in moved_lines
        ))
        self.assertTrue(any("PAD RIGHT=0 A=0" in line for line in moved_lines))


class ScreenRendererTests(unittest.TestCase):
    def test_dummy_screen_renders_dynamic_avatar(self):
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        world = load_world(PIT)
        renderer = ScreenRenderer(world)
        try:
            self.assertEqual((renderer.width, renderer.height), (1280, 768))
            self.assertEqual(renderer.screen.get_size(), (1280, 768))
            first = renderer.pygame.image.tostring(
                renderer.present(_view(world, x=128)), "RGBA")
            second = renderer.pygame.image.tostring(
                renderer.present(_view(world, x=192)), "RGBA")
            self.assertNotEqual(first, second)
            self.assertEqual(renderer.static_scene.get_size(), (1280, 768))
        finally:
            renderer.close()
        self.assertFalse(renderer.pygame.display.get_init())

    def test_terminal_labels_and_overlay_are_presentation_only(self):
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        world = load_world(PIT)
        renderer = ScreenRenderer(world)
        try:
            self.assertIsNone(terminal_label(None))
            normal = renderer.pygame.image.tostring(
                renderer.present(_view(world, x=128, y=384)), "RGBA")
            self.assertIsNone(renderer._terminal_fonts)
            for terminal, label in (("success", "VICTORY"), ("dead", "GAME OVER"),
                                    ("timeout", "TIME OUT")):
                with self.subTest(terminal=terminal):
                    self.assertEqual(terminal_label(terminal), label)
                    result = renderer.pygame.image.tostring(
                        renderer.present(_view(world, x=128, y=384,
                                               terminal=terminal, alive=terminal != "dead")),
                        "RGBA")
                    self.assertNotEqual(result, normal)
            self.assertIsNotNone(renderer._terminal_fonts)
            with self.assertRaises(ValueError):
                terminal_label("won")
        finally:
            renderer.close()


if __name__ == "__main__":
    unittest.main()
