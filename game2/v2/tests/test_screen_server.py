from __future__ import annotations

import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from game2.v2.contracts.framing import recv_frame, send_frame
from game2.v2.contracts.manifests import Endpoint
from game2.v2.contracts.screen import ScreenSourceDiscovery
from game2.v2.contracts.screen_server import (
    ScreenServerDiscovery, bind_message, decode_screen_server_request,
    decode_screen_server_status, probe_message, status_message, unbind_message,
)
from game2.v2.management.screen_server import ScreenServer


class FakeViewer:
    def __init__(self):
        self.returncode = None
    def poll(self): return self.returncode
    def terminate(self): self.returncode = 0
    def wait(self, timeout=None): return self.returncode
    def kill(self): self.returncode = -9


class ScreenServerContractTests(unittest.TestCase):
    def test_probe_bind_unbind_and_status_are_strict(self):
        source = ScreenSourceDiscovery(
            1, "session", "pit", Endpoint("127.0.0.1", 12345), 1280, 768
        )
        self.assertEqual(decode_screen_server_request(probe_message()), "screen_server_probe")
        self.assertEqual(decode_screen_server_request(bind_message(2, source)), "screen_server_bind")
        self.assertEqual(decode_screen_server_request(unbind_message(2)), "screen_server_unbind")
        screens = decode_screen_server_status(status_message(2, {2: source}))
        self.assertEqual(screens[0]["state"], "idle")
        self.assertEqual(screens[1]["state"], "bound")
        self.assertEqual(screens[1]["session_id"], "session")


class ScreenServerRuntimeTests(unittest.TestCase):
    def test_bind_is_detachable_and_server_is_independent(self):
        launched = []
        def launch(number, source):
            launched.append((number, source))
            return FakeViewer()

        with tempfile.TemporaryDirectory() as directory:
            discovery_path = Path(directory) / "screen-server.json"
            server = ScreenServer(slots=2, viewer_launcher=launch)
            errors=[]
            def run():
                try: server.run(discovery_path)
                except BaseException as exc: errors.append(exc)
            worker=threading.Thread(target=run,daemon=True); worker.start()
            self.assertTrue(server.ready.wait(2))
            discovery=ScreenServerDiscovery.from_file(discovery_path)
            source=ScreenSourceDiscovery(
                1,"session","pit",Endpoint("127.0.0.1",12345),1280,768
            )
            client=socket.create_connection(
                (discovery.endpoint.host,discovery.endpoint.port),timeout=1
            )
            try:
                client.settimeout(1)
                send_frame(client,bind_message(1,source))
                status=decode_screen_server_status(recv_frame(client))
                self.assertEqual(status[0]["state"],"bound")
                self.assertEqual(launched,[(1,source)])
                send_frame(client,unbind_message(1))
                status=decode_screen_server_status(recv_frame(client))
                self.assertEqual(status[0]["state"],"idle")
            finally:
                client.close()
            server.request_stop(); worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            self.assertFalse(errors)
            deadline=time.monotonic()+1
            while discovery_path.exists() and time.monotonic()<deadline:
                time.sleep(0.01)
            self.assertFalse(discovery_path.exists())


if __name__ == "__main__":
    unittest.main()
