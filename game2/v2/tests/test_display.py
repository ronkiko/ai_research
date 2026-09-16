from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time
import unittest
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

from game2.v2.console.config import DisplayManifest, SessionConfig
from game2.v2.console.display.display import DisplayService
from game2.v2.console.display.screen.autotile import AutoTiler, NeighborMask
from game2.v2.console.display.screen.renderer import ScreenRenderer
from game2.v2.console.display.view_state import AvatarView, DisplayState
from game2.v2.console.display.vision.renderer import VisionClass, VisionFrame, VisionRenderer
from game2.v2.console.main import run_session
from game2.v2.console.world import Rect, TileID, WorldDefinition, load_world
from game2.v2.contracts.framing import encode_frame
from game2.v2.contracts.manifests import Endpoint


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


def _view(world, x=2, y=2, tick=0):
    return DisplayState("session", tick, world.map_id, AvatarView(x, y, True))


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
        state = _view(world)
        frame = VisionRenderer().render(world, state)
        self.assertEqual((frame.width, frame.height, frame.tick), (8, 6, 0))
        self.assertEqual(frame.pixels[0:8], bytes((0, 0, 1, 1, 2, 2, 4, 4)))
        self.assertEqual(frame.pixels[2 * frame.width + 2], VisionClass.AVATAR)
        self.assertEqual(set(frame.pixels), {0, 1, 2, 3, 4})

    def test_avatar_motion_changes_bytes_without_mutating_inputs(self):
        world = _tiny_world()
        first_state = _view(world, x=2, tick=4)
        second_state = _view(world, x=3, tick=5)
        original_world, original_state = world, first_state
        renderer = VisionRenderer(world)
        first = renderer.render(first_state)
        second = renderer.render(second_state)
        self.assertNotEqual(first.pixels, second.pixels)
        self.assertEqual(first.pixels[2 * first.width + 2], VisionClass.AVATAR)
        self.assertEqual(second.pixels[2 * second.width + 3], VisionClass.AVATAR)
        self.assertEqual(world, original_world)
        self.assertEqual(first_state, original_state)

    def test_frame_is_immutable_and_does_not_leak_physics_metadata(self):
        frame = VisionRenderer().render(_tiny_world(), _view(_tiny_world()))
        self.assertEqual({field.name for field in fields(frame)},
                         {"width", "height", "pixels", "session_tick"})
        self.assertIs(type(frame.pixels), bytes)
        for name in ("vx", "vy", "grounded", "accepted_actions", "late_actions",
                     "collision_rects"):
            self.assertFalse(hasattr(frame, name), name)
        with self.assertRaises(FrozenInstanceError):
            frame.pixels = b""

    def test_vision_source_has_no_pygame_dependency(self):
        for source in (V2 / "console" / "display" / "vision").rglob("*.py"):
            self.assertNotIn("pygame", source.read_text(encoding="utf-8"))


class DisplayServiceTests(unittest.TestCase):
    def _manifest(self, mode="vision"):
        return DisplayManifest("session", Endpoint("127.0.0.1", 1), "unused-map", mode)

    def test_vision_consumes_valid_state_and_ignores_invalid_payloads(self):
        world = _tiny_world()
        service = DisplayService(self._manifest(), world=world)
        valid = {
            "version": 1, "type": "state", "session_id": "session",
            "session_tick": 7, "map": "tiny",
            "avatar": {"x": 2, "y": 2, "alive": True},
        }
        self.assertTrue(service.consume(valid))
        self.assertEqual(service.frames_received, 1)
        self.assertIsInstance(service.latest_frame, VisionFrame)
        for invalid in (
            {**valid, "type": "telemetry"},
            {**valid, "session_id": "other"},
            {**valid, "map": "other"},
            {**valid, "avatar": {"x": "bad", "y": 2}},
        ):
            self.assertFalse(service.consume(invalid))
        self.assertEqual(service.frames_received, 1)
        service.renderer.close()

    def test_screen_mode_uses_the_same_world_and_renderer_capability(self):
        calls = []

        class RecordingRenderer:
            def render(self, view):
                calls.append(view)
                return view

        world = _tiny_world()
        service = DisplayService(self._manifest("screen"), world=world,
                                 renderer=RecordingRenderer())
        self.assertTrue(service.consume({
            "type": "state", "session_id": "session", "tick": 2,
            "map": "tiny", "avatar": {"x": 2, "y": 2},
        }))
        self.assertEqual(calls[0].map_id, world.map_id)
        self.assertEqual(service.frames_received, 1)

    def test_ingest_keeps_only_newest_monotonic_valid_state(self):
        world = _tiny_world()
        service = DisplayService(self._manifest(), world=world)
        valid = {
            "version": 1, "type": "state", "session_id": "session",
            "session_tick": 10, "map": "tiny",
            "avatar": {"x": 2, "y": 2, "alive": True},
        }
        self.assertTrue(service.ingest(valid))
        self.assertFalse(service.ingest({**valid, "session_tick": 9}))
        self.assertFalse(service.ingest({**valid, "session_tick": 11,
                                         "avatar": {"x": "bad", "y": 2}}))
        self.assertEqual(service.latest_state.session_tick, 10)
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
                self.calls.append(view.session_tick)
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
                "session_tick": 1, "map": "tiny", "avatar": {"x": 2, "y": 2},
            }))
            self.assertTrue(renderer.first_render_started.wait(1))
            for tick in range(2, 6):
                client.sendall(encode_frame({
                    "version": 1, "type": "state", "session_id": "session",
                    "session_tick": tick, "map": "tiny",
                    "avatar": {"x": 2 + tick, "y": 2},
                }))

            deadline = time.monotonic() + 1
            while service.frames_received < 5 and time.monotonic() < deadline:
                time.sleep(0.001)
            self.assertEqual(service.frames_received, 5)
            self.assertTrue(runner.is_alive())
            self.assertEqual(service.latest_state.session_tick, 5)
            self.assertEqual(renderer.calls, [1])

            renderer.release.set()
            client.close()
            client = None
            runner.join(2)
            self.assertFalse(runner.is_alive())
            self.assertEqual(renderer.calls, [1, 5])
            self.assertEqual(service.latest_frame.session_tick, 5)
        finally:
            renderer.release.set()
            if client is not None:
                client.close()
            runner.join(2)
            listener.close()

    def test_display_manifest_requires_mode_and_world_resource(self):
        manifest = self._manifest()
        self.assertEqual(DisplayManifest.from_dict(manifest.to_dict()), manifest)
        with self.assertRaises(ValueError):
            DisplayManifest.from_dict({**manifest.to_dict(), "mode": "debug"})
        with self.assertRaises(ValueError):
            DisplayManifest.from_dict({**manifest.to_dict(), "mode": []})
        with self.assertRaises(ValueError):
            DisplayManifest.from_dict({**manifest.to_dict(), "world_file": ""})

    def test_screen_demo_is_explicit_realtime_opt_in(self):
        config = SessionConfig.from_file(V2 / "console" / "configs" / "screen-demo.json")
        self.assertEqual((config.clock_mode, config.enable_display, config.display_mode),
                         ("realtime", True, "screen"))

    def test_rendering_mode_does_not_change_authoritative_result(self):
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        base = json.loads((V2 / "console" / "configs" / "realtime-smoke.json").read_text())
        base["map"] = str(PIT)
        base["session_ticks"] = 120
        results = []
        with tempfile.TemporaryDirectory() as directory:
            for mode in ("disabled", "vision", "screen"):
                config = {**base, "enable_display": mode != "disabled",
                          "display_mode": "vision" if mode != "screen" else "screen"}
                path = Path(directory) / f"{mode}.json"
                path.write_text(json.dumps(config), encoding="utf-8")
                status, summary = run_session(path)
                self.assertEqual(status, 0)
                results.append(summary["avatar"])
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0], results[2])

    def test_display_startup_failure_does_not_stop_engine(self):
        old_driver = os.environ.get("SDL_VIDEODRIVER")
        os.environ["SDL_VIDEODRIVER"] = "v2-invalid-driver"
        try:
            base = json.loads((V2 / "console" / "configs" / "realtime-smoke.json").read_text())
            base.update({"map": str(PIT), "session_ticks": 120,
                         "display_mode": "screen", "enable_display": True})
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "screen-failure.json"
                path.write_text(json.dumps(base), encoding="utf-8")
                status, summary = run_session(path)
            self.assertEqual(status, 0)
            self.assertEqual(summary["session_ticks"], 120)
        finally:
            if old_driver is None:
                os.environ.pop("SDL_VIDEODRIVER", None)
            else:
                os.environ["SDL_VIDEODRIVER"] = old_driver


class ScreenRendererTests(unittest.TestCase):
    def test_dummy_screen_loads_v2_assets_and_dynamic_avatar(self):
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

    def test_screen_runtime_uses_v2_assets_only(self):
        renderer_source = (V2 / "console" / "display" / "screen" / "renderer.py")
        source = renderer_source.read_text(encoding="utf-8")
        self.assertNotIn("game2/assets", source)
        self.assertNotIn("../../../../assets", source)
        for name in ("BG1.png", "BG2.png", "BG3.png", "Tileset.png", "Decors.png"):
            self.assertTrue((renderer_source.parent / "assets" / name).is_file(), name)

    def test_pygame_import_is_confined_to_screen(self):
        for source in V2.rglob("*.py"):
            if "tests" in source.parts or "screen" in source.parts:
                continue
            text = source.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"(?m)^\s*(?:from|import)\s+pygame(?:\s|$)",
                                str(source))


if __name__ == "__main__":
    unittest.main()
