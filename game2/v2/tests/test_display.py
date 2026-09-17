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
from typing import Any
from unittest import mock

from game2.v2.console.config import DisplayManifest, SessionConfig
from game2.v2.console.display.display import DisplayService
from game2.v2.console.display.screen.autotile import AutoTiler, NeighborMask
from game2.v2.console.display.screen.renderer import (CHECKER_CELL_SIZE, CHECKER_COLUMNS,
                                                       CHECKER_ROWS, ScreenRenderer,
                                                       terminal_label)
from game2.v2.console.display.view_state import ActorView, DisplayState
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


def _view(world, x=2, y=2, world_tick=0, terminal=None, alive=True):
    actor = ActorView("actor-a", "player-a", x, y, 0, 0, True, alive, terminal)
    return DisplayState("session", world_tick, world.map_id, (actor,), "actor-a")


def _actor_payload(x: Any = 2, y: Any = 2, actor_id="actor-a", player_id="player-a", **extra):
    return {"actor_id": actor_id, "player_id": player_id, "x": x, "y": y,
            "vx": 0, "vy": 0, "grounded": True, "alive": True,
            "result": None, **extra}


class DisplayStateTests(unittest.TestCase):
    def test_terminal_roundtrip_accepts_only_authoritative_results(self):
        world = _tiny_world()
        base = {
            "type": "state", "session_id": "session", "world_tick": 1,
            "map": "tiny", "actors": [_actor_payload()],
        }
        for terminal in (None, "success", "dead", "timeout"):
            with self.subTest(terminal=terminal):
                state = DisplayState.from_payload({**base,
                                                   "actors": [_actor_payload(result=terminal)]},
                                                   "session", world)
                self.assertEqual(state.self_actor.result, terminal)

        for invalid in ("won", "death", 1, False):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    DisplayState.from_payload({**base,
                                               "actors": [_actor_payload(result=invalid)]},
                                               "session", world)

    def test_terminal_direct_value_is_validated(self):
        world = _tiny_world()
        with self.assertRaises(ValueError):
            DisplayState("session", 0, world.map_id,
                         (ActorView("actor-a", "player-a", 2, 2, 0, 0,
                                    True, True, "won"),), "actor-a")


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
        self.assertEqual((frame.width, frame.height, frame.world_tick), (8, 6, 0))
        self.assertEqual(frame.pixels[0:8], bytes((0, 0, 1, 1, 2, 2, 4, 4)))
        self.assertEqual(frame.pixels[2 * frame.width + 2], VisionClass.AVATAR)
        self.assertEqual(set(frame.pixels), {0, 1, 2, 3, 4})

    def test_avatar_motion_changes_bytes_without_mutating_inputs(self):
        world = _tiny_world()
        first_state = _view(world, x=2, world_tick=4)
        second_state = _view(world, x=3, world_tick=5)
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
                         {"width", "height", "pixels", "world_tick"})
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
            "world_tick": 7, "map": "tiny",
            "actors": [_actor_payload()],
        }
        self.assertTrue(service.consume(valid))
        self.assertEqual(service.frames_received, 1)
        self.assertIsInstance(service.latest_frame, VisionFrame)
        for invalid in (
            {**valid, "type": "telemetry"},
            {**valid, "session_id": "other"},
            {**valid, "map": "other"},
            {**valid, "actors": [_actor_payload(x="bad")]},
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
            "type": "state", "session_id": "session", "world_tick": 2,
            "map": "tiny", "actors": [_actor_payload()],
        }))
        self.assertEqual(calls[0].map_id, world.map_id)
        self.assertEqual(service.frames_received, 1)

    def test_ingest_keeps_only_newest_monotonic_valid_state(self):
        world = _tiny_world()
        service = DisplayService(self._manifest(), world=world)
        valid = {
            "version": 1, "type": "state", "session_id": "session",
            "world_tick": 10, "map": "tiny",
            "actors": [_actor_payload()],
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

    def test_embedded_demo_keeps_state_and_disables_display_process(self):
        config = SessionConfig.from_file(V2 / "console" / "configs" / "embedded-demo.json")
        self.assertEqual((config.clock_mode, config.enable_state,
                          config.enable_telemetry, config.enable_display),
                         ("realtime", True, True, False))

    def test_rendering_mode_does_not_change_authoritative_result(self):
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        base = json.loads((V2 / "console" / "configs" / "realtime-smoke.json").read_text())
        base["map"] = str(PIT)
        base["world_ticks"] = 120
        results = []
        with tempfile.TemporaryDirectory() as directory:
            for mode in ("disabled", "vision", "screen"):
                config = {**base, "enable_display": mode != "disabled",
                          "display_mode": "vision" if mode != "screen" else "screen"}
                path = Path(directory) / f"{mode}.json"
                path.write_text(json.dumps(config), encoding="utf-8")
                status, summary = run_session(path)
                self.assertEqual(status, 0)
                results.append(summary["actors"])
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0], results[2])

    def test_display_startup_failure_does_not_stop_engine(self):
        old_driver = os.environ.get("SDL_VIDEODRIVER")
        os.environ["SDL_VIDEODRIVER"] = "v2-invalid-driver"
        try:
            base = json.loads((V2 / "console" / "configs" / "realtime-smoke.json").read_text())
            base.update({"map": str(PIT), "world_ticks": 120,
                         "display_mode": "screen", "enable_display": True})
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "screen-failure.json"
                path.write_text(json.dumps(base), encoding="utf-8")
                status, summary = run_session(path)
            self.assertEqual(status, 0)
            self.assertEqual(summary["world_ticks"], 120)
        finally:
            if old_driver is None:
                os.environ.pop("SDL_VIDEODRIVER", None)
            else:
                os.environ["SDL_VIDEODRIVER"] = old_driver


class ScreenRendererTests(unittest.TestCase):
    def test_checkered_finish_flag_matches_goal_geometry_without_mutating_world(self):
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        world = load_world(PIT)
        original = world
        renderer = ScreenRenderer(world)
        try:
            flag = renderer._build_goal()
            pole_x = world.goal.x + min(16, max(8, world.goal.width // 8))
            flag_left, flag_top = pole_x + 3, world.goal.y + 4
            light = flag.get_at((flag_left + 4, flag_top + 4))
            dark = flag.get_at((flag_left + CHECKER_CELL_SIZE + 4, flag_top + 4))
            self.assertNotEqual(light, dark)
            self.assertGreater(light[3], 0)
            self.assertGreater(flag.get_at((pole_x, world.goal.y + 48))[3], 0)
            self.assertLessEqual(flag_left + CHECKER_COLUMNS * CHECKER_CELL_SIZE,
                                 world.goal.x + world.goal.width)
            self.assertLessEqual(flag_top + CHECKER_ROWS * CHECKER_CELL_SIZE,
                                 world.goal.y + world.goal.height)
            self.assertEqual(world, original)
        finally:
            renderer.close()

    def test_embedded_renderer_uses_target_without_display_or_event_ownership(self):
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        import pygame

        pygame.display.init()
        target = pygame.Surface((1280, 768))
        try:
            with mock.patch.object(pygame.display, "set_mode",
                                   wraps=pygame.display.set_mode) as set_mode, \
                    mock.patch.object(pygame.display, "flip",
                                      wraps=pygame.display.flip) as flip, \
                    mock.patch.object(pygame.event, "get",
                                      wraps=pygame.event.get) as get_events:
                renderer = ScreenRenderer(load_world(PIT), target_surface=target,
                                          pygame_module=pygame)
                try:
                    self.assertFalse(renderer.owns_display)
                    self.assertIs(renderer.screen, target)
                    renderer.present(_view(load_world(PIT), x=192))
                    renderer.present(_view(load_world(PIT), x=192, terminal="success"))
                    self.assertFalse(renderer.poll_close())
                    self.assertEqual(set_mode.call_count, 0)
                    flip.assert_not_called()
                    get_events.assert_not_called()
                    self.assertTrue(pygame.display.get_init())
                finally:
                    renderer.close()
        finally:
            pygame.display.quit()

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

    def test_pygame_import_is_confined_to_screen_or_human_player(self):
        for source in V2.rglob("*.py"):
            if ("tests" in source.parts or "screen" in source.parts or
                    "human" in source.parts or source.name in {"demo.py", "vision_demo.py"}):
                continue
            text = source.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"(?m)^\s*(?:from|import)\s+pygame(?:\s|$)",
                                str(source))

    def test_screen_static_scene_keeps_avatar_goal_and_hazard_visible(self):
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        world = load_world(PIT)
        renderer = ScreenRenderer(world)
        try:
            static = renderer.static_scene
            self.assertNotEqual(static.get_at((9 * 64 + 32, 10 * 64 + 48)),
                                static.get_at((7 * 64 + 32, 10 * 64 + 48)))
            self.assertGreater(static.get_at((world.goal.x + 24, world.goal.y + 8))[0], 200)
            before = static.get_at((128 + 32, 384 + 32))
            after = renderer.present(_view(world, x=128, y=384)).get_at((128 + 32, 384 + 32))
            self.assertNotEqual(before, after)
        finally:
            renderer.close()

    def test_terminal_labels_and_overlay_are_presentation_only(self):
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        world = load_world(PIT)
        renderer = ScreenRenderer(world)
        try:
            self.assertIsNone(terminal_label(None))
            normal = renderer.pygame.image.tostring(
                renderer.present(_view(world, x=128, y=384)), "RGBA")
            self.assertIsNone(renderer._terminal_fonts)
            for terminal, label in (
                ("success", "VICTORY"),
                ("dead", "GAME OVER"),
                ("timeout", "TIME OUT"),
            ):
                with self.subTest(terminal=terminal):
                    self.assertEqual(terminal_label(terminal), label)
                    result = renderer.pygame.image.tostring(
                        renderer.present(_view(world, x=128, y=384,
                                               terminal=terminal,
                                               alive=terminal != "dead")), "RGBA")
                    self.assertNotEqual(result, normal)
            self.assertIsNotNone(renderer._terminal_fonts)
            with self.assertRaises(ValueError):
                terminal_label("won")
        finally:
            renderer.close()


if __name__ == "__main__":
    unittest.main()
