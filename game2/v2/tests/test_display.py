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
from game2.v2.console.display.view_state import ActorView, DisplayState
from game2.v2.console.display.vision.renderer import VisionClass, VisionRenderer
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


class VisionRendererTests(unittest.TestCase):
    def test_semantic_raster_contains_static_goal_and_dynamic_avatar(self):
        world = _tiny_world()
        frame = VisionRenderer().render(world, _view(world))
        self.assertEqual((frame.width, frame.height, frame.world_tick), (8, 6, 0))
        self.assertEqual(frame.pixels[0:8], bytes((0, 0, 1, 1, 2, 2, 4, 4)))
        self.assertEqual(frame.pixels[2 * frame.width + 2], VisionClass.AVATAR)
        self.assertEqual(set(frame.pixels), {0, 1, 2, 3, 4})

    def test_public_vision_is_immutable_and_does_not_leak_physics_metadata(self):
        frame = VisionRenderer().render(_tiny_world(), _view(_tiny_world()))
        self.assertEqual({field.name for field in fields(frame)},
                         {"width", "height", "pixels", "world_tick"})
        self.assertIs(type(frame.pixels), bytes)
        for name in ("vx", "vy", "grounded", "accepted_actions", "late_actions",
                     "collision_rects"):
            self.assertFalse(hasattr(frame, name), name)
        with self.assertRaises(FrozenInstanceError):
            frame.pixels = b""


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
