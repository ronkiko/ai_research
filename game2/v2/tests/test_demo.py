from __future__ import annotations

import os
import time
import unittest
from pathlib import Path
from unittest import mock

from game2.v2 import demo
from game2.v2.console.config import DisplayManifest, OperatorControlManifest
from game2.v2.console.transport.control_server import ControlServer
from game2.v2.contracts.manifests import Endpoint, PeripheralManifest
from game2.v2.demo_control import DemoControlClient

ROOT = Path(__file__).resolve().parents[3]
V2 = ROOT / "game2" / "v2"
PIT = V2 / "console" / "world" / "maps" / "pit.json"


class DemoTests(unittest.TestCase):
    def test_demo_control_client_sends_actor_local_respawn_and_waits_for_ack(self):
        server = ControlServer("127.0.0.1", 0)
        server.start()
        client = DemoControlClient(OperatorControlManifest(
            "demo-session", Endpoint(server.host, server.port)))
        try:
            client.connect()
            client.request_respawn()
            deadline = time.monotonic() + 1
            while not server.commands.qsize() and time.monotonic() < deadline:
                time.sleep(0.001)
            envelope = server.drain()[0]
            self.assertEqual(envelope.command.actor_id, "compatibility-actor")
            server.respond(envelope.client_id, {
                "version": 1, "type": "respawn_ack",
                "actor_id": "compatibility-actor", "status": "accepted",
                "world_tick": 10,
            })
            self.assertEqual(client.wait_respawn_ack(1)["status"], "accepted")
        finally:
            client.close()
            server.close()

    def test_r_is_operator_only_and_is_not_repeated_while_held(self):
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        import pygame

        class FakeClient:
            connected = True
            failed = False
            error = None

            def close(self):
                return None

        class FakeControl:
            connected = True
            failed = False
            error = None

            def __init__(self):
                self.requests = 0

            def request_respawn(self, _actor_id=None):
                self.requests += 1

            def close(self):
                return None

        class FakeRenderer:
            def __init__(self, world, target_surface, pygame_module):
                self.world = world

            def close(self):
                return None

        class FakeDisplayService:
            latest_state = None

            def __init__(self, *args, **kwargs):
                return None

            def start(self):
                return None

            def close(self):
                return None

        capability = DisplayManifest(
            "demo-session", Endpoint("127.0.0.1", 23457), str(PIT), "screen")
        manifest = PeripheralManifest("demo-session", Endpoint("127.0.0.1", 23456))
        control = FakeControl()
        pygame.quit()
        try:
            with mock.patch.object(demo, "ScreenRenderer", FakeRenderer), \
                    mock.patch.object(demo, "DisplayService", FakeDisplayService):
                shell = demo.DemoShell(capability, manifest, client=FakeClient(),
                                       control_client=control, pygame_module=pygame)
                try:
                    shell._handle_event(pygame.event.Event(
                        pygame.KEYDOWN, {"key": pygame.K_d}))
                    shell._handle_event(pygame.event.Event(
                        pygame.KEYDOWN, {"key": pygame.K_SPACE}))
                    self.assertEqual((shell.keyboard.state.right, shell.keyboard.state.jump),
                                     (True, True))
                    restart = pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_r})
                    shell._handle_event(restart)
                    for _ in range(10):
                        shell._handle_event(restart)
                    self.assertEqual(control.requests, 1)
                    self.assertEqual((shell.keyboard.state.right, shell.keyboard.state.jump),
                                     (False, False))
                    shell._handle_event(pygame.event.Event(
                        pygame.KEYUP, {"key": pygame.K_r}))
                    shell._handle_event(restart)
                    self.assertEqual(control.requests, 2)
                finally:
                    shell.close()
        finally:
            pygame.quit()


if __name__ == "__main__":
    unittest.main()
