from __future__ import annotations

import io
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import fields
from pathlib import Path
from unittest import mock

from game2.v2 import vision_demo
from game2.v2.console.config import SessionConfig
from game2.v2.console.transport.publisher import VisionPublisher
from game2.v2.contracts.framing import ProtocolError, encode_frame, recv_exact
from game2.v2.contracts.manifests import Endpoint, PeripheralManifest
from game2.v2.contracts.vision import (VisionFrame, recv_vision_frame,
                                        send_vision_frame)
from game2.v2.console.display.display import VISION_HZ


ROOT = Path(__file__).resolve().parents[3]
V2 = ROOT / "game2" / "v2"
PIT = V2 / "console" / "world" / "maps" / "pit.json"


class VisionContractTests(unittest.TestCase):
    def test_validation_uses_c_level_bytes_check_instead_of_python_pixel_generator(self):
        source = (V2 / "contracts" / "vision.py").read_text(encoding="utf-8")
        self.assertIn("pixels.translate", source)
        self.assertNotIn("any(value", source)

    def test_raw_frame_round_trip_has_only_public_observation_fields(self):
        left, right = socket.socketpair()
        frame = VisionFrame(2, 2, bytes((0, 1, 2, 5)), 123)
        try:
            send_vision_frame(left, "session", frame)
            received = recv_vision_frame(right, "session")
        finally:
            left.close()
            right.close()
        self.assertEqual(received, frame)
        self.assertEqual({field.name for field in fields(received)},
                         {"width", "height", "pixels", "world_tick"})
        for hidden in ("x", "y", "vx", "vy", "grounded", "reward", "telemetry"):
            self.assertFalse(hasattr(received, hidden), hidden)

    def test_header_is_json_but_pixels_are_not_json_or_base64(self):
        left, right = socket.socketpair()
        try:
            send_vision_frame(left, "session", VisionFrame(2, 1, b"\x00\x04", 1))
            prefix = right.recv(4)
            header_size = int.from_bytes(prefix, "big")
            header = recv_exact(right, header_size)
            pixels = recv_exact(right, 2)
        finally:
            left.close()
            right.close()
        self.assertIn(b'"type":"vision_frame"', header)
        self.assertIn(b'"world_tick":1', header)
        self.assertNotIn(b'"session_tick"', header)
        self.assertNotIn(b'"pixels"', header)
        self.assertEqual(pixels, b"\x00\x04")

    def test_receive_rejects_wrong_session_extra_fields_and_unknown_class(self):
        valid = {
            "version": 1, "type": "vision_frame", "session_id": "session",
            "world_tick": 1, "width": 1, "height": 1,
            "pixel_format": "u8-semantic", "byte_length": 1,
        }
        for header, expected, pixels in (
            (valid, "other", b"\x00"),
            ({**valid, "extra": True}, "session", b"\x00"),
            ({**valid, "session_tick": 1}, "session", b"\x00"),
            ({**valid, "episode_tick": 1}, "session", b"\x00"),
            (valid, "session", b"\x06"),
        ):
            left, right = socket.socketpair()
            try:
                left.sendall(encode_frame(header) + pixels)
                with self.assertRaises(ProtocolError):
                    recv_vision_frame(right, expected)
            finally:
                left.close()
                right.close()

    def test_dimensions_are_bounded_by_generic_raw_receive_limit(self):
        with self.assertRaises(ProtocolError):
            VisionFrame(1025, 1025, b"", 0)


class VisionPublisherTests(unittest.TestCase):
    def test_multiple_subscribers_keep_independent_latest_slots(self):
        publisher = VisionPublisher("127.0.0.1", 0, "session")
        publisher.start()
        fast = socket.create_connection((publisher.host, publisher.port), timeout=1)
        slow = socket.create_connection((publisher.host, publisher.port), timeout=1)
        try:
            deadline = time.monotonic() + 1
            while publisher.subscriber_count() < 2 and time.monotonic() < deadline:
                time.sleep(0.001)
            self.assertEqual(publisher.subscriber_count(), 2)
            pixels = b"\x00" * (1280 * 768)
            frames = [VisionFrame(1280, 768, pixels, tick) for tick in range(8)]
            started = time.monotonic()
            for frame in frames:
                self.assertTrue(publisher.publish(frame))
            self.assertLess(time.monotonic() - started, 0.5)

            fast.settimeout(2)
            latest = None
            while latest is None or latest.world_tick < 7:
                latest = recv_vision_frame(fast, "session")
            self.assertGreaterEqual(latest.world_tick, 7)
        finally:
            fast.close()
            slow.close()
            publisher.close()


class PublicVisionIntegrationTests(unittest.TestCase):
    def test_examiner_window_is_ready_before_scripted_player_launch(self):
        events = []
        manifest = PeripheralManifest(
            "session", Endpoint("127.0.0.1", 23456), Endpoint("127.0.0.1", 23457))
        ready = "READY " + json.dumps(manifest.to_dict()) + "\n"

        class FakeProcess:
            def __init__(self, output=None):
                self.stdout = io.StringIO(output or "")
                self.returncode = None
                self.pid = None

            def poll(self):
                return self.returncode

            def terminate(self):
                self.returncode = 0

            def wait(self, timeout=None):
                return self.returncode

        class FakeStream:
            def __init__(self, public_manifest):
                self.manifest = public_manifest
                self.frames_received = 1
                self.connected = True

            def connect(self):
                events.append("vision-connected")

            def wait_for_frame(self, _timeout):
                events.append("initial-frame")
                return VisionFrame(2, 2, b"\x00\x03\x01\x01", 0)

            def close(self):
                return None

        class FakeExaminer:
            def __init__(self, stream, player, console, **kwargs):
                self.player = player

            def initialize(self, frame):
                self.frame = frame
                events.append("window-ready")

            def set_player_process(self, process, *, ready=False):
                events.append(("player-attached", ready))

            def run(self, **kwargs):
                events.append("examiner-running")
                return 0

            def close(self):
                return None

        console = FakeProcess(ready)
        player = FakeProcess('READY {"session_id":"session","vision":true}\n')

        def console_factory(command, **kwargs):
            events.append("console-launched")
            return console

        def player_factory(command, **kwargs):
            events.append("player-launched")
            return player

        with mock.patch.object(vision_demo, "VisionStream", FakeStream):
            status = vision_demo.run_vision_demo(
                popen_factory=console_factory, player_factory=player_factory,
                examiner_factory=FakeExaminer, startup_timeout=1, shutdown_timeout=1)

        self.assertEqual(status, 0)
        self.assertLess(events.index("window-ready"), events.index("player-launched"))
        self.assertLess(events.index("initial-frame"), events.index("player-launched"))
        attached = next(event for event in events
                        if isinstance(event, tuple) and event[0] == "player-attached")
        self.assertEqual(attached, ("player-attached", True))

    def test_console_publishes_public_frame_and_scripted_player_receives_it(self):
        config = json.loads((V2 / "console" / "configs" / "vision-demo.json").read_text())
        config.update({"map": str(PIT), "world_ticks": 360})
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            config_path = directory / "vision.json"
            manifest_path = directory / "peripheral.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            console = subprocess.Popen(
                [sys.executable, "-m", "game2.v2.console.main", "--config", str(config_path)],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            viewer = player = None
            try:
                manifest = vision_demo.wait_console_ready(console, timeout=10,
                                                          output=io.StringIO())
                self.assertIsNotNone(manifest.vision)
                manifest.write(manifest_path)
                viewer = socket.create_connection((manifest.vision.host, manifest.vision.port),
                                                  timeout=2)
                player = subprocess.Popen(
                    [sys.executable, "-m", "game2.v2.player.scripted.main",
                     "--manifest", str(manifest_path), "--ticks", "40"],
                    cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True,
                )
                first = recv_vision_frame(viewer, manifest.session_id)
                second = recv_vision_frame(viewer, manifest.session_id)
                self.assertEqual((first.width, first.height), (1280, 768))
                self.assertEqual(len(first.pixels), first.width * first.height)
                self.assertTrue(set(first.pixels) <= {0, 1, 2, 3, 4, 5})
                self.assertGreater(second.world_tick, first.world_tick)
                self.assertEqual(player.wait(timeout=15), 0)
                player_output = player.stdout.read() if player.stdout else ""
                self.assertIn('"vision": true', player_output)
                self.assertRegex(player_output, r"vision_frames=[1-9][0-9]*")
                self.assertEqual(console.wait(timeout=15), 0)
            finally:
                if viewer is not None:
                    viewer.close()
                if player is not None and player.poll() is None:
                    player.terminate()
                    player.wait(timeout=5)
                if player is not None and player.stdout is not None:
                    player.stdout.close()
                if console.poll() is None:
                    console.terminate()
                    console.wait(timeout=5)
                if console.stdout is not None:
                    console.stdout.close()

    def test_scripted_player_crosses_pit_using_public_vision_process_path(self):
        config = json.loads((V2 / "console" / "configs" / "vision-demo.json").read_text())
        config.update({"map": str(PIT), "world_ticks": 1000})
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            config_path = directory / "vision-pit.json"
            manifest_path = directory / "peripheral.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            console = subprocess.Popen(
                [sys.executable, "-m", "game2.v2.console.main", "--config", str(config_path)],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            player = None
            try:
                manifest = vision_demo.wait_console_ready(console, timeout=10,
                                                          output=io.StringIO())
                manifest.write(manifest_path)
                player = subprocess.Popen(
                    [sys.executable, "-m", "game2.v2.player.scripted.main",
                     "--manifest", str(manifest_path), "--ticks", "500"],
                    cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True,
                )
                self.assertEqual(player.wait(timeout=12), 0)
                self.assertEqual(console.wait(timeout=12), 0)
                summary = (V2 / "console" / "runs" / manifest.session_id /
                           "summary.json").read_text(encoding="utf-8")
                self.assertEqual(json.loads(summary)["actors"][0]["result"], "success")
            finally:
                if player is not None and player.poll() is None:
                    player.terminate()
                    player.wait(timeout=5)
                if console.poll() is None:
                    console.terminate()
                    console.wait(timeout=5)
                if player is not None and player.stdout is not None:
                    player.stdout.close()
                if console.stdout is not None:
                    console.stdout.close()

    def test_vision_demo_has_no_private_state_or_renderer_dependency(self):
        source = (V2 / "vision_demo.py").read_text(encoding="utf-8")
        for forbidden in ("EngineManifest", "InternalManifest", "DisplayManifest",
                          "WorldState", "TelemetrySnapshot", "VisionRenderer", "send_frame"):
            self.assertNotIn(forbidden, source)
        self.assertIn("recv_vision_frame", source)
        self.assertIn("pygame.display.set_mode", source)

    def test_examiner_colorizes_public_classes_and_owns_one_window(self):
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        import pygame

        class FakeProcess:
            returncode = None

            def poll(self):
                return self.returncode

        class FakeStream:
            manifest = PeripheralManifest("session", Endpoint("127.0.0.1", 1),
                                          Endpoint("127.0.0.1", 2))
            latest_received_at = time.monotonic()
            frames_received = 1
            connected = True

            def __init__(self):
                self.latest = VisionFrame(2, 2, bytes((0, 1, 2, 4)), 7)

            def wait_for_frame(self, _timeout):
                return self.latest

        pygame.quit()
        stream = FakeStream()
        examiner = vision_demo.VisionExaminer(stream, FakeProcess(), FakeProcess(),
                                              pygame_module=pygame,
                                              sleeper=lambda _delay: pygame.event.post(
                                                  pygame.event.Event(pygame.QUIT)))
        try:
            with mock.patch.object(pygame.display, "set_mode",
                                   wraps=pygame.display.set_mode) as set_mode:
                self.assertEqual(examiner.run(1), 0)
                self.assertEqual(set_mode.call_count, 1)
            self.assertEqual(examiner.frame_surface.get_at((0, 0))[:3], vision_demo.PALETTE[0])
            self.assertEqual(examiner.frame_surface.get_at((1, 0))[:3], vision_demo.PALETTE[1])
            self.assertEqual(examiner.frame_surface.get_at((0, 1))[:3], vision_demo.PALETTE[2])
            self.assertEqual(examiner.frame_surface.get_at((1, 1))[:3], vision_demo.PALETTE[4])
        finally:
            examiner.close()

    def test_vision_demo_config_and_wrapper_are_explicit(self):
        config = SessionConfig.from_file(V2 / "console" / "configs" / "vision-demo.json")
        self.assertEqual((config.clock_mode, config.physics_hz, config.enable_state,
                          config.enable_telemetry, config.enable_display,
                          config.display_mode),
                         ("realtime", 120, True, True, True, "vision"))
        script = V2 / "vision.sh"
        self.assertTrue(stat.S_IMODE(script.stat().st_mode) & stat.S_IXUSR)
        source = script.read_text(encoding="utf-8")
        self.assertIn("set -Eeuo pipefail", source)
        self.assertIn("${PYTHON:-python3}", source)
        self.assertIn("-m game2.v2.vision_demo", source)

    def test_vision_clock_is_explicitly_bounded_below_engine_clock(self):
        config = SessionConfig.from_file(V2 / "console" / "configs" / "vision-demo.json")
        self.assertEqual(config.physics_hz, 120)
        self.assertEqual(VISION_HZ, 30)
        source = (V2 / "console" / "display" / "display.py").read_text(encoding="utf-8")
        self.assertIn("vision_period = 1 / VISION_HZ", source)
        examiner = (V2 / "vision_demo.py").read_text(encoding="utf-8")
        self.assertIn("Vision FPS", examiner)
        self.assertNotIn("Viewer FPS", examiner)


if __name__ == "__main__":
    unittest.main()
