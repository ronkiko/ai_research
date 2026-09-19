"""Explicit persistent Console lifecycle smoke.

Run this only for server, attach, or Player lifecycle changes, not through the
default unit-test discovery.
"""
from __future__ import annotations

import json
import select
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from game2.v2.player.connection import PlayerConnection
from game2.v2.player.peripherals import VisionReceiver
from game2.v2.contracts.connection import ATTACH, attach_message, probe_message, validate_probe_response
from game2.v2.contracts.discovery import ConsoleDiscovery
from game2.v2.contracts.framing import recv_frame, send_frame
from game2.v2.contracts.manifests import PlayerManifest
from game2.v2.contracts.screen import ScreenSourceDiscovery, recv_screen_frame
from game2.v2.console.config import InternalManifest

ROOT = Path(__file__).resolve().parents[3]
V2 = ROOT / "game2" / "v2"


def _wait_process_ready(process: subprocess.Popen, timeout: float = 10.0) -> tuple[dict, list[str]]:
    if process.stdout is None:
        raise RuntimeError("process stdout is unavailable")
    deadline = time.monotonic() + timeout
    output: list[str] = []
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("process did not announce READY")
        readable, _, _ = select.select([process.stdout], [], [], remaining)
        if not readable:
            raise TimeoutError("process did not announce READY")
        line = process.stdout.readline()
        if not line:
            raise RuntimeError("process exited before READY")
        output.append(line)
        if line.startswith("READY "):
            return json.loads(line[6:]), output


def _wait_for_discovery(process: subprocess.Popen, path: Path,
                        timeout: float = 10.0) -> ConsoleDiscovery:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Console exited before discovery")
        if path.exists():
            return ConsoleDiscovery.from_file(path)
        time.sleep(0.01)
    raise TimeoutError("Console did not publish discovery")


def _wait_until(predicate, timeout: float, interval: float = 0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    value = predicate()
    if value:
        return value
    raise AssertionError("condition did not become true before timeout")


def _vision_bounds(frame, semantic_class: int):
    points = [(index % frame.width, index // frame.width)
              for index, value in enumerate(frame.pixels) if value == semantic_class]
    if not points:
        return None
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs), max(ys)


class PersistentConsoleSmoke(unittest.TestCase):
    """Exercise the real persistent Console, Engine, Controller, and Display."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="game2-v2-server-test-")
        cls.discovery_path = Path(cls.temp.name) / "current-console.json"
        cls.screen_discovery_path = Path(cls.temp.name) / "screen-source.json"
        config_path = V2 / "console" / "configs" / "server.json"
        cls.console = subprocess.Popen(
            [sys.executable, "-m", "game2.v2.console.main", "--server",
             "--config", str(config_path), "--discovery", str(cls.discovery_path)],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        try:
            cls.discovery = _wait_for_discovery(cls.console, cls.discovery_path, timeout=30)
            _wait_until(cls.screen_discovery_path.exists, 10)
            cls.screen_source = ScreenSourceDiscovery.from_file(cls.screen_discovery_path)
            cls.run_dir = V2 / "console" / "runs" / cls.discovery.session_id
            internal_path = cls.run_dir / "internal-manifest.json"
            _wait_until(internal_path.exists, 5)
            cls.internal = InternalManifest.from_file(internal_path)
            cls.telemetry = socket.create_connection(
                (cls.internal.engine_telemetry.host, cls.internal.engine_telemetry.port),
                timeout=2)
            cls.telemetry.settimeout(0.25)
        except BaseException:
            cls._stop_console()
            cls.temp.cleanup()
            raise

    @classmethod
    def _stop_console(cls):
        telemetry = getattr(cls, "telemetry", None)
        if telemetry is not None:
            telemetry.close()
        console = getattr(cls, "console", None)
        if console is not None:
            if console.poll() is None:
                console.terminate()
                try:
                    console.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    console.kill()
                    console.wait(timeout=5)
            if console.stdout is not None:
                console.stdout.close()

    @classmethod
    def tearDownClass(cls):
        cls._stop_console()
        cls.temp.cleanup()

    def _wait_telemetry(self, predicate, timeout: float = 3.0):
        return _wait_until(lambda: self._read_telemetry(predicate), timeout)

    def _read_telemetry(self, predicate):
        deadline = time.monotonic() + 0.25
        while time.monotonic() < deadline:
            try:
                payload = recv_frame(self.telemetry)
            except socket.timeout:
                return None
            if predicate(payload):
                return payload
        return None

    def _probe(self):
        probe = socket.create_connection(
            (self.discovery.attach.host, self.discovery.attach.port), timeout=1)
        try:
            probe.settimeout(1)
            send_frame(probe, probe_message())
            response = recv_frame(probe)
            validate_probe_response(response, self.discovery.session_id)
            self.assertEqual(response["attach"], self.discovery.attach.as_dict())
            return response
        finally:
            probe.close()

    def _player_directories(self):
        players = self.run_dir / "players"
        return tuple(players.iterdir()) if players.exists() else ()

    def _attach_player(self):
        connection = PlayerConnection(self.discovery, connect_timeout=8)
        stream = None
        try:
            manifest = connection.connect()
            self.assertEqual(set(manifest.to_dict()), {
                "session_id", "player_id", "actor_id", "joystick", "vision"})
            joystick = socket.create_connection((manifest.joystick.host, manifest.joystick.port),
                                                timeout=2)
            joystick.close()
            stream = VisionReceiver(manifest, connect_timeout=8)
            stream.connect()
            first = stream.wait_for_frame(8)
            return connection, stream, manifest, first
        except BaseException:
            if stream is not None:
                stream.close()
            connection.close()
            raise

    @staticmethod
    def _wait_frame(stream, predicate, timeout: float = 8.0):
        return _wait_until(lambda: stream.latest
                           if stream.latest is not None and predicate(stream.latest) else None,
                           timeout)

    @staticmethod
    def _drive_right(manifest: PlayerManifest, count: int = 60):
        joystick = socket.create_connection((manifest.joystick.host, manifest.joystick.port),
                                            timeout=2)
        try:
            for sequence in range(1, count + 1):
                send_frame(joystick, {"version": 1, "type": "joystick",
                                      "sequence": sequence, "right": True, "jump": False})
        finally:
            joystick.close()

    def test_real_persistent_lifecycle_and_pending_attach_cleanup(self):
        connections = []
        streams = []
        scripted = None
        try:
            self.assertIsNone(self.console.poll())
            screen = socket.create_connection(
                (self.screen_source.endpoint.host, self.screen_source.endpoint.port), timeout=2)
            try:
                screen.settimeout(8)
                frame = recv_screen_frame(screen, self.screen_source.session_id)
                self.assertEqual((frame.width, frame.height), (1280, 768))
            finally:
                screen.close()
            initial = self._wait_telemetry(lambda payload:
                                           payload.get("actors") == [])
            self._probe()
            zero_tick = initial["world_tick"]
            advanced = self._wait_telemetry(
                lambda payload: payload.get("actors") == [] and
                payload.get("world_tick", -1) > zero_tick)
            self.assertGreater(advanced["world_tick"], zero_tick)

            pending = socket.create_connection(
                (self.discovery.attach.host, self.discovery.attach.port), timeout=2)
            send_frame(pending, attach_message())
            pending.close()
            self.assertTrue(_wait_until(lambda: bool(self._player_directories()), 5))
            self.assertTrue(_wait_until(lambda: not self._player_directories(), 12))
            empty_after_pending = self._wait_telemetry(
                lambda payload: payload.get("actors") == [])
            self.assertEqual(empty_after_pending["actors"], [])
            self.assertIsNone(self.console.poll())
            self._probe()

            connection_a, stream_a, manifest_a, first_a = self._attach_player()
            connections.append(connection_a)
            streams.append(stream_a)
            self.assertNotEqual(manifest_a.player_id, manifest_a.actor_id)
            self.assertNotIn(3, first_a.pixels)
            later_a = self._wait_frame(stream_a,
                                       lambda frame: frame.world_tick > first_a.world_tick)
            self.assertGreater(later_a.world_tick, first_a.world_tick)
            before_start_tick = later_a.world_tick

            self.assertTrue(connection_a.request_start())
            started_a = self._wait_frame(
                stream_a, lambda frame: frame.world_tick > before_start_tick and
                3 in frame.pixels)
            self.assertGreater(started_a.world_tick, before_start_tick)
            self.assertIsNone(self.console.poll())
            actor_started = self._wait_telemetry(
                lambda payload: any(actor.get("actor_id") == manifest_a.actor_id
                                    for actor in payload.get("actors", [])))
            self.assertEqual(actor_started["session_id"], self.discovery.session_id)

            manifest_path = Path(self.temp.name) / "scripted-a.json"
            manifest_a.write(manifest_path)
            scripted = subprocess.Popen(
                [sys.executable, "-m", "game2.v2.player.scripted.main",
                 "--manifest", str(manifest_path), "--ticks", "500"],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            ready, _ = _wait_process_ready(scripted, timeout=8)
            self.assertEqual(ready["session_id"], self.discovery.session_id)
            terminal = _wait_until(
                lambda: connection_a.latest_event
                if connection_a.latest_event is not None else None, 15)
            self.assertEqual(terminal["event"], "terminal")
            self.assertIn(terminal["result"], {"success", "dead", "timeout"})
            self.assertGreaterEqual(terminal["world_tick"], before_start_tick)
            scripted.terminate()
            scripted.wait(timeout=5)
            if scripted.stdout is not None:
                scripted.stdout.close()
            scripted = None

            before_respawn = terminal["world_tick"]
            self.assertTrue(connection_a.request_respawn())
            respawned_a = self._wait_frame(
                stream_a, lambda frame: frame.world_tick >= before_respawn and
                3 in frame.pixels)
            self.assertGreaterEqual(respawned_a.world_tick, before_respawn)
            respawn_state = self._wait_telemetry(
                lambda payload: any(actor.get("actor_id") == manifest_a.actor_id and
                                    actor.get("result") is None
                                    for actor in payload.get("actors", [])))
            self.assertTrue(any(actor["actor_id"] == manifest_a.actor_id and
                                actor["result"] is None
                                for actor in respawn_state["actors"]))

            connection_a.detach()
            connection_a.close()
            stream_a.close()
            self.assertTrue(_wait_until(lambda: not self._player_directories(), 8))
            self.assertIsNone(self.console.poll())
            self._probe()

            connection_b, stream_b, manifest_b, first_b = self._attach_player()
            connections.append(connection_b)
            streams.append(stream_b)
            self.assertEqual(manifest_b.session_id, self.discovery.session_id)
            self.assertNotEqual(manifest_b.player_id, manifest_a.player_id)
            self.assertNotEqual(manifest_b.actor_id, manifest_a.actor_id)
            self.assertGreater(first_b.world_tick, respawned_a.world_tick)
            self.assertNotIn(3, first_b.pixels)

            connection_b.detach()
            connection_b.close()
            stream_b.close()
            self.assertTrue(_wait_until(lambda: not self._player_directories(), 8))
            connection_c = PlayerConnection(self.discovery, connect_timeout=8)
            connection_d = PlayerConnection(self.discovery, connect_timeout=8)
            connections.extend((connection_c, connection_d))
            attached = {}
            errors = []

            def attach_concurrently(name, connection):
                try:
                    attached[name] = connection.connect()
                except BaseException as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=attach_concurrently, args=("c", connection_c)),
                       threading.Thread(target=attach_concurrently, args=("d", connection_d))]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=12)
            self.assertFalse(errors)
            self.assertEqual(set(attached), {"c", "d"})
            manifest_c, manifest_d = attached["c"], attached["d"]
            self.assertEqual(len({manifest_c.player_id, manifest_d.player_id}), 2)
            self.assertEqual(len({manifest_c.actor_id, manifest_d.actor_id}), 2)

            stream_c = VisionReceiver(manifest_c, connect_timeout=8)
            stream_d = VisionReceiver(manifest_d, connect_timeout=8)
            streams.extend((stream_c, stream_d))
            stream_c.connect()
            stream_d.connect()
            first_c = stream_c.wait_for_frame(8)
            first_d = stream_d.wait_for_frame(8)
            self.assertNotIn(3, first_c.pixels)
            self.assertNotIn(3, first_d.pixels)
            two_before_start = max(first_c.world_tick, first_d.world_tick)
            self.assertTrue(connection_c.request_start())
            self.assertTrue(connection_d.request_start())
            running_c = self._wait_frame(stream_c, lambda frame: 3 in frame.pixels)
            running_d = self._wait_frame(stream_d, lambda frame: 3 in frame.pixels)
            self.assertGreaterEqual(min(running_c.world_tick, running_d.world_tick),
                                    two_before_start)
            self._drive_right(manifest_d)
            visible_c = self._wait_frame(
                stream_c, lambda frame: 3 in frame.pixels and 5 in frame.pixels)
            visible_d = self._wait_frame(
                stream_d, lambda frame: 3 in frame.pixels and 5 in frame.pixels)
            self.assertLess(_vision_bounds(visible_c, 3)[0],
                            _vision_bounds(visible_c, 5)[0])
            self.assertGreater(_vision_bounds(visible_d, 3)[0],
                               _vision_bounds(visible_d, 5)[0])
            self.assertIsNone(self.console.poll())

            connection_c.detach()
            connection_c.close()
            stream_c.close()
            self.assertTrue(_wait_until(lambda: len(self._player_directories()) <= 1, 8))
            self.assertTrue(connection_d.connected)
            self._probe()
        finally:
            if scripted is not None:
                scripted.terminate()
                scripted.wait(timeout=5)
                if scripted.stdout is not None:
                    scripted.stdout.close()
            for stream in streams:
                stream.close()
            for connection in connections:
                connection.close()


if __name__ == "__main__":
    unittest.main()
