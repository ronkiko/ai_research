from __future__ import annotations

import socketserver
import threading
import unittest

from gameclient.v1.host.protocol import LineReader as HostLineReader, encode_line as host_encode, message as host_message
from gameclient.v1.host.config import HOST_EVENT_LIMIT, HOST_MAX_ID_CHARS
from gameclient.v1.host.server import HostService
from gameclient.v1.protocol import LineReader as GatewayLineReader, encode_line as gateway_encode, message as gateway_message
from gameclient.v1.clients.base import HostClient


class _FakeGatewayHandler(socketserver.BaseRequestHandler):
    def handle(self):
        reader = GatewayLineReader()
        while True:
            try:
                request = reader.recv(self.request)
            except EOFError:
                return
            kind = request["type"]
            if kind == "list_players":
                response = gateway_message("players", players=["player1", "player2", "player3"])
            elif kind == "login":
                response = gateway_message(
                    "login",
                    session_id="session-1",
                    player_id=request["player_id"],
                    entity_id="actor-player1",
                    world_id="world1",
                    zone_id=self.server.zone_id,
                )
            elif kind == "snapshot":
                response = gateway_message(
                    "snapshot",
                    session_id="session-1",
                    zone_id=self.server.zone_id,
                    observation=(
                        {
                            "entity_id": "actor-player1",
                            "zone_id": self.server.zone_id,
                            "world_epoch": self.server.world_epoch,
                            "world_revision": self.server.world_revision,
                        }
                        if self.server.controller_generation is not None else None
                    ),
                    controller=(
                        {
                            "controller_id": "controller.player1",
                            "generation": self.server.controller_generation,
                        }
                        if self.server.controller_generation is not None else None
                    ),
                    receipts=list(self.server.receipts),
                    snapshot={
                        "version": 1,
                        "type": "zone_snapshot",
                        "zone_id": self.server.zone_id,
                        "world_tick": 42,
                        "physics_hz": 120,
                        "line_length": 1000.0,
                        "entities": [
                            {"entity_id": "actor-player1", "kind": "player", "owner_id": "player1", "x": 100.0, "vx": 0.0, "move_x": 0},
                            {"entity_id": "mob1", "kind": "mob", "owner_id": None, "x": 900.0, "vx": 0.0, "move_x": 0},
                        ],
                        "commands_applied": [],
                    },
                )
            elif kind == "input":
                self.server.sequences.append(request["sequence"])
                self.server.input_fences.append((
                    request.get("expected_zone_id"),
                    request.get("controller_generation"),
                    request.get("expected_world_epoch"),
                ))
                response = gateway_message(
                    "command_queued",
                    command_id=len(self.server.sequences),
                    world_tick=43,
                )
            elif kind == "reset":
                self.server.resets += 1
                response = gateway_message(
                    "command_queued",
                    command_id=100 + self.server.resets,
                    world_tick=44,
                )
            elif kind == "logout":
                response = gateway_message("logout", player_id="player1")
            else:
                response = gateway_message("error", error=f"unexpected {kind}")
            self.request.sendall(gateway_encode(response))


class _FakeGateway(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _FakeGatewayHandler)
        self.sequences = []
        self.resets = 0
        self.zone_id = "zone1"
        self.controller_generation = None
        self.world_epoch = None
        self.world_revision = None
        self.receipts = []
        self.input_fences = []


class HostVerticalTests(unittest.TestCase):
    def setUp(self):
        self.gateway = _FakeGateway()
        self.gateway_thread = threading.Thread(target=self.gateway.serve_forever, daemon=True)
        self.gateway_thread.start()
        self.host = HostService(
            host="127.0.0.1",
            port=0,
            gateway_host="127.0.0.1",
            gateway_port=self.gateway.server_address[1],
        )
        self.host_thread = threading.Thread(target=self.host.serve_forever, daemon=True)
        self.host_thread.start()

    def tearDown(self):
        self.host.shutdown()
        self.host_thread.join(timeout=1)
        self.gateway.shutdown()
        self.gateway.server_close()
        self.gateway_thread.join(timeout=1)

    def client(self, client_id):
        return HostClient(client_id, host=self.host.address[0], port=self.host.address[1], timeout=1.0)

    def test_p100_b900_and_shared_sequence_and_events(self):
        cli = self.client("cli")
        mcp = self.client("mcp")
        gui = self.client("gui")
        try:
            cli.login("player1")
            state = gui.state()
            entities = {e["entity_id"]: e for e in state["snapshot"]["entities"]}
            self.assertEqual(entities["actor-player1"]["x"], 100.0)
            self.assertEqual(entities["mob1"]["x"], 900.0)
            first = mcp.input(1)
            second = gui.input(-1)
            self.assertEqual((first["sequence"], second["sequence"]), (1, 2))
            self.assertEqual(self.gateway.sequences, [1, 2])
            events = cli.events(0)["events"]
            self.assertEqual(
                [(e["client_id"], e.get("move_x")) for e in events if e["kind"] == "input"],
                [("mcp", 1), ("gui", -1)],
            )
        finally:
            cli.close()
            mcp.close()
            gui.close()

    def test_reset_preserves_session_and_sequence(self):
        operator = self.client("operator")
        lab = self.client("gamelab")
        try:
            login = operator.login("player1")
            session_id = login["session"]["session_id"]
            first = operator.input(1)
            self.assertEqual(first["sequence"], 1)

            reset = lab.reset()
            self.assertEqual(reset["sequence"], 1)
            self.assertEqual(self.gateway.resets, 1)

            session = operator.session()
            self.assertEqual(session["session_id"], session_id)
            self.assertEqual(session["sequence"], 1)

            second = operator.input(0)
            self.assertEqual(second["sequence"], 2)
            events = operator.events(0, limit=20)["events"]
            reset_events = [event for event in events if event["kind"] == "reset"]
            self.assertEqual(len(reset_events), 1)
            self.assertEqual(reset_events[0]["client_id"], "gamelab")
            self.assertEqual(reset_events[0]["sequence"], 1)
        finally:
            operator.close()
            lab.close()

    def test_host_follows_authoritative_zone_and_controller_fence(self):
        client = self.client("cli")
        try:
            client.login("player1")
            self.gateway.zone_id = "laboratory"
            self.gateway.controller_generation = 4
            self.gateway.world_epoch = "epoch-new"
            self.gateway.world_revision = 9
            self.gateway.receipts = [{"action_id": "transfer.1", "status": "applied"}]

            state = client.state()
            self.assertEqual(state["session"]["zone_id"], "laboratory")
            self.assertEqual(state["session"]["controller_generation"], 4)
            self.assertEqual(state["observation"]["world_epoch"], "epoch-new")
            self.assertEqual(state["controller"]["generation"], 4)
            self.assertEqual(state["receipts"][0]["action_id"], "transfer.1")

            client.motor(0.25)
            self.assertEqual(
                self.gateway.input_fences[-1], ("laboratory", 4, "epoch-new")
            )
            events = client.events(0, limit=20)["events"]
            transfers = [event for event in events if event["kind"] == "zone_transfer"]
            self.assertEqual(len(transfers), 1)
            self.assertEqual(transfers[0]["source_zone"], "zone1")
            self.assertEqual(transfers[0]["target_zone"], "laboratory")
        finally:
            client.close()

    def test_event_history_is_memory_bounded_and_page_bounded(self):
        client = self.client("cli")
        try:
            for index in range(HOST_EVENT_LIMIT + 25):
                self.host._append_event(
                    "test",
                    client_id="cli",
                    sequence=index + 1,
                    move_x=0,
                )
            self.assertEqual(len(self.host._events), HOST_EVENT_LIMIT)
            page = client.events(0, limit=7)
            self.assertLessEqual(len(page["events"]), 7)
            self.assertTrue(page["truncated_before"])
            self.assertTrue(page["has_more"])
        finally:
            client.close()

    def test_client_and_player_ids_are_bounded(self):
        too_long = "x" * (HOST_MAX_ID_CHARS + 1)
        client = self.client(too_long)
        try:
            with self.assertRaisesRegex(Exception, "client_id must be at most"):
                client.health()
        finally:
            client.close()

        normal = self.client("cli")
        try:
            with self.assertRaisesRegex(Exception, "player_id must be at most"):
                normal.login(too_long)
        finally:
            normal.close()

    def test_non_loopback_host_bind_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "loopback only"):
            HostService(
                host="0.0.0.0",
                port=0,
                gateway_host="127.0.0.1",
                gateway_port=self.gateway.server_address[1],
            )

    def test_one_client_connection_can_issue_multiple_requests(self):
        host, port = self.host.address
        import socket
        with socket.create_connection((host, port), timeout=1.0) as sock:
            reader = HostLineReader()
            sock.sendall(host_encode(host_message("health", client_id="test")))
            self.assertEqual(reader.recv(sock)["type"], "health")
            sock.sendall(host_encode(host_message("describe", client_id="test")))
            self.assertEqual(reader.recv(sock)["type"], "describe")


if __name__ == "__main__":
    unittest.main()
