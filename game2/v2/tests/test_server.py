from __future__ import annotations

import json
import stat
import tempfile
import unittest
from pathlib import Path

from game2.v2.contracts.connection import (
    ATTACH,
    DETACH,
    PROBE,
    RESPAWN,
    START,
    attach_message,
    decode_connection_message,
    detach_message,
    probe_message,
    respawn_message,
    start_message,
)
from game2.v2.contracts.discovery import ConsoleDiscovery, publish_current_console
from game2.v2.contracts.framing import ProtocolError
from game2.v2.contracts.manifests import Endpoint, PlayerManifest
from game2.v2.console.config import SessionConfig


ROOT = Path(__file__).resolve().parents[3]
V2 = ROOT / "game2" / "v2"


class PublicConnectionContractTests(unittest.TestCase):
    def test_lifecycle_requests_are_strict_and_target_free(self):
        requests = (
            (PROBE, probe_message()), (ATTACH, attach_message()),
            (START, start_message()), (RESPAWN, respawn_message()),
            (DETACH, detach_message()),
        )
        for expected, message in requests:
            self.assertEqual(decode_connection_message(message), expected)
            with self.assertRaises(ProtocolError):
                decode_connection_message({**message, "actor_id": "foreign-actor"})

    def test_player_manifest_is_distinct_and_does_not_expose_private_channels(self):
        manifest = PlayerManifest("session", "player-1", "actor-1",
                                 Endpoint("127.0.0.1", 1), Endpoint("127.0.0.1", 2))
        self.assertEqual(PlayerManifest.from_dict(manifest.to_dict()), manifest)
        self.assertEqual(set(manifest.to_dict()), {
            "session_id", "player_id", "actor_id", "joystick", "vision"})
        with self.assertRaises(ValueError):
            PlayerManifest.from_dict({**manifest.to_dict(), "engine_control": {}})


class DiscoveryTests(unittest.TestCase):
    def test_discovery_is_strict_and_atomic(self):
        discovery = ConsoleDiscovery(1, "session", "pit", Endpoint("127.0.0.1", 12345))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "current-console.json"
            publish_current_console(discovery, path)
            self.assertEqual(ConsoleDiscovery.from_file(path), discovery)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["type"],
                             "console_discovery")
            with self.assertRaises(ValueError):
                ConsoleDiscovery.from_dict({**discovery.to_dict(), "extra": True})


class ServerEntryPointTests(unittest.TestCase):
    def test_server_config_and_launchers_are_canonical(self):
        config = SessionConfig.from_file(V2 / "console" / "configs" / "server.json")
        self.assertEqual((config.clock_mode, config.physics_hz, config.world_ticks),
                         ("realtime", 120, None))
        self.assertTrue(stat.S_IMODE((V2 / "boot.sh").stat().st_mode) & stat.S_IXUSR)
        boot = (V2 / "boot.sh").read_text(encoding="utf-8")
        self.assertIn("set -Eeuo pipefail", boot)
        self.assertIn("--server", boot)
        vision = (V2 / "vision_demo.py").read_text(encoding="utf-8")
        self.assertNotIn("game2.v2.console.main", vision)
        self.assertNotIn("run_session", vision)
        self.assertNotIn("launch_console", vision)


if __name__ == "__main__":
    unittest.main()
