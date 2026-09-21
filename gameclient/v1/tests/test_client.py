from __future__ import annotations

import json
from pathlib import Path
import socketserver
import tempfile
import threading
import unittest

from gameclient.v1.client import GameClient
from gameclient.v1.protocol import LineReader, encode_line, message
from gameclient.v1.state import SessionError, SessionStore


class _Gateway:
    def __init__(self):
        self.tick = 100
        self.x = 180.0
        self.session = None
        self.last_sequence = 0

    def dispatch(self, request: dict) -> dict:
        kind = request["type"]
        if kind == "health":
            return message("health", component="gateway", status="ready")
        if kind == "list_players":
            return message("players", players=["player1", "player2", "player3"])
        if kind == "login":
            self.session = "session-test"
            return message(
                "login_ok", session_id=self.session, player_id=request["player_id"],
                entity_id="actor-" + request["player_id"], world_id="world1", zone_id="zone1"
            )
        if kind == "input":
            if request["session_id"] != self.session:
                return message("error", error="unknown session")
            if request["sequence"] <= self.last_sequence:
                return message("error", error="sequence must increase")
            self.last_sequence = request["sequence"]
            self.x += float(request["move_x"])
            self.tick += 1
            return message("command_queued", command_id=self.last_sequence, world_tick=self.tick)
        if kind == "snapshot":
            return message(
                "snapshot",
                session_id=self.session,
                zone_id="zone1",
                snapshot=message(
                    "zone_snapshot", zone_id="zone1", world_tick=self.tick, physics_hz=120,
                    entities=[{
                        "entity_id": "actor-player1", "kind": "player", "owner_id": "player1",
                        "x": self.x, "y": 300.0, "vx": 0.0, "vy": 0.0,
                        "move_x": 0, "move_y": 0,
                    }], commands_applied=[]
                )
            )
        if kind == "logout":
            return message(
                "logout_ok", session_id=self.session, player_id="player1",
                entity_id="actor-player1", world_id="world1", zone_id="zone1"
            )
        return message("error", error="unsupported")


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        reader = LineReader()
        try:
            request = reader.recv(self.request)
        except EOFError:
            return
        response = self.server.gateway.dispatch(request)  # type: ignore[attr-defined]
        self.request.sendall(encode_line(response))


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.gateway = _Gateway()
        self.server = _Server(("127.0.0.1", 0), _Handler)
        self.server.gateway = self.gateway  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.temp = tempfile.TemporaryDirectory()
        self.session_path = Path(self.temp.name) / "session.json"
        self.client = GameClient(
            host="127.0.0.1", port=self.server.server_address[1], timeout=1.0,
            session_file=self.session_path,
        )

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1.0)
        self.temp.cleanup()

    def test_complete_public_gateway_flow(self):
        self.assertEqual(self.client.players(), ["player1", "player2", "player3"])
        session = self.client.login("player1")
        self.assertEqual(session["entity_id"], "actor-player1")
        before = self.client.snapshot()
        result = self.client.move("right")
        after = self.client.snapshot()
        self.assertEqual(result["sequence"], 1)
        self.assertGreater(after["entities"][0]["x"], before["entities"][0]["x"])
        stopped = self.client.move("stop")
        self.assertEqual(stopped["sequence"], 2)
        self.client.logout()
        self.assertFalse(self.session_path.exists())

    def test_sequence_is_reserved_before_rpc_and_persists(self):
        self.client.login("player1")
        self.client.input(1, 0)
        again = GameClient(
            host="127.0.0.1", port=self.server.server_address[1], timeout=1.0,
            session_file=self.session_path,
        )
        result = again.input(0, 1)
        self.assertEqual(result["sequence"], 2)
        stored = json.loads(self.session_path.read_text(encoding="utf-8"))
        self.assertEqual(stored["sequence"], 2)

    def test_login_requires_local_logout_first(self):
        self.client.login("player1")
        with self.assertRaisesRegex(SessionError, "already logged in locally"):
            self.client.login("player2")

    def test_snapshot_requires_login(self):
        with self.assertRaisesRegex(SessionError, "not logged in"):
            self.client.snapshot()


class SessionStoreTests(unittest.TestCase):
    def test_reserve_sequence_is_monotonic(self):
        with tempfile.TemporaryDirectory() as temp:
            store = SessionStore(Path(temp) / "session.json")
            store.save_login({
                "session_id": "s", "player_id": "p", "entity_id": "e",
                "world_id": "w", "zone_id": "z",
            })
            _, first = store.reserve_sequence()
            _, second = store.reserve_sequence()
            self.assertEqual((first, second), (1, 2))


class IndependenceTests(unittest.TestCase):
    def test_client_python_does_not_import_gameserver(self):
        root = Path(__file__).resolve().parents[1]
        for path in root.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("from gameserver", source, path.name)
            self.assertNotIn("import gameserver", source, path.name)


if __name__ == "__main__":
    unittest.main()
