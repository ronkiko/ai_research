from __future__ import annotations

import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest import mock

from game2.v2 import vision_demo
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
    player_event,
)
from game2.v2.contracts.discovery import ConsoleDiscovery, publish_current_console
from game2.v2.contracts.framing import ProtocolError, recv_frame, send_frame
from game2.v2.contracts.manifests import Endpoint, PlayerManifest
from game2.v2.player import connection as player_connection


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


class PlayerConnectionDeadlineTests(unittest.TestCase):
    def test_vision_demo_reuses_shared_player_connection(self):
        self.assertIs(vision_demo.PlayerConnection, player_connection.PlayerConnection)

    def test_attach_wait_retries_socket_timeouts_inside_one_deadline(self):
        client, server = socket.socketpair()
        manifest = PlayerManifest("session", "player", "actor",
                                  Endpoint("127.0.0.1", 1), Endpoint("127.0.0.1", 2))
        discovery = ConsoleDiscovery(1, "session", "pit", Endpoint("127.0.0.1", 3))
        errors = []

        def delayed_manifest():
            try:
                self.assertEqual(recv_frame(server)["type"], ATTACH)
                time.sleep(1.1)
                send_frame(server, {"version": 1, "type": "player_manifest",
                                    **manifest.to_dict()})
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=delayed_manifest)
        worker.start()
        connection = vision_demo.PlayerConnection(discovery, connect_timeout=2.5)
        try:
            with mock.patch.object(player_connection, "_connect", return_value=client):
                self.assertEqual(connection.connect(), manifest)
        finally:
            connection.close()
            server.close()
            worker.join(timeout=2)
        self.assertFalse(errors)
        self.assertIsNone(connection._socket)

    def test_attach_timeout_closes_socket_without_starting_reader(self):
        client, server = socket.socketpair()
        discovery = ConsoleDiscovery(1, "session", "pit", Endpoint("127.0.0.1", 3))

        def consume_request():
            try:
                recv_frame(server)
            except (EOFError, OSError, ValueError):
                pass

        worker = threading.Thread(target=consume_request)
        worker.start()
        connection = vision_demo.PlayerConnection(discovery, connect_timeout=0.45)
        started = time.monotonic()
        try:
            with mock.patch.object(player_connection, "_connect", return_value=client):
                with self.assertRaises(TimeoutError):
                    connection.connect()
        finally:
            connection.close()
            server.close()
            worker.join(timeout=1)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertIsNone(connection._thread)
        self.assertIsNone(connection._socket)

    def test_shared_connection_reads_terminal_event_and_sends_detach(self):
        client, server = socket.socketpair()
        manifest = PlayerManifest("session", "player", "actor",
                                  Endpoint("127.0.0.1", 1), Endpoint("127.0.0.1", 2))
        discovery = ConsoleDiscovery(1, "session", "pit", Endpoint("127.0.0.1", 3))
        errors = []

        def lifecycle_server():
            try:
                self.assertEqual(recv_frame(server)["type"], ATTACH)
                send_frame(server, {"version": 1, "type": "player_manifest",
                                    **manifest.to_dict()})
                send_frame(server, player_event("dead", 7))
                self.assertEqual(recv_frame(server)["type"], DETACH)
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=lifecycle_server)
        worker.start()
        connection = vision_demo.PlayerConnection(discovery)
        try:
            with mock.patch.object(player_connection, "_connect", return_value=client):
                self.assertEqual(connection.connect(), manifest)
            self.assertEqual(connection.wait_for_terminal(1), player_event("dead", 7))
            connection.detach()
        finally:
            connection.close()
            server.close()
            worker.join(timeout=2)
        self.assertFalse(errors)


if __name__ == "__main__":
    unittest.main()
