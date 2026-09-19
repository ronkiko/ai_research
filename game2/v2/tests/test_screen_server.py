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
    BIND,
    OPEN,
    SLOT_ATTACH,
    SLOT_DETACH,
    SLOT_OPENED,
    ScreenServerDiscovery,
    bind_message,
    decode_screen_server_request,
    decode_screen_server_status,
    decode_screen_slot_message,
    open_message,
    probe_message,
    status_message,
    unbind_message,
)
from game2.v2.management.screen_server import ScreenServer


def _request(discovery: ScreenServerDiscovery, message: dict):
    sock = socket.create_connection(
        (discovery.endpoint.host, discovery.endpoint.port), timeout=1
    )
    try:
        sock.settimeout(1)
        send_frame(sock, message)
        return recv_frame(sock)
    finally:
        sock.close()


class ScreenServerContractTests(unittest.TestCase):
    def test_open_bind_unbind_and_status_contract(self):
        source = ScreenSourceDiscovery(
            1, "session", "pit", Endpoint("127.0.0.1", 12345), 1280, 768
        )
        self.assertEqual(decode_screen_server_request(open_message(1)), OPEN)
        self.assertEqual(decode_screen_server_request(bind_message(1, source)), BIND)
        screens = decode_screen_server_status(
            status_message(2, {1}, {1: source})
        )
        self.assertEqual(screens[0]["state"], "bound")
        self.assertEqual(screens[1]["state"], "closed")


class ScreenServerRuntimeTests(unittest.TestCase):
    def test_foreground_screen_registers_then_training_only_rebinds_source(self):
        with tempfile.TemporaryDirectory() as directory:
            discovery_path = Path(directory) / "screen-server.json"
            server = ScreenServer(slots=2)
            errors = []

            def run():
                try:
                    server.run(discovery_path)
                except BaseException as exc:
                    errors.append(exc)

            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            self.assertTrue(server.ready.wait(2))
            discovery = ScreenServerDiscovery.from_file(discovery_path)

            initial = decode_screen_server_status(
                _request(discovery, probe_message())
            )
            self.assertEqual(initial[0]["state"], "closed")

            viewer = socket.create_connection(
                (discovery.endpoint.host, discovery.endpoint.port), timeout=1
            )
            viewer.settimeout(1)
            send_frame(viewer, open_message(1))
            opened = recv_frame(viewer)
            self.assertEqual(
                decode_screen_slot_message(opened, 1), SLOT_OPENED
            )

            waiting = decode_screen_server_status(
                _request(discovery, probe_message())
            )
            self.assertEqual(waiting[0]["state"], "waiting")

            source = ScreenSourceDiscovery(
                1, "session", "pit", Endpoint("127.0.0.1", 12345), 1280, 768
            )
            bound = decode_screen_server_status(
                _request(discovery, bind_message(1, source))
            )
            self.assertEqual(bound[0]["state"], "bound")
            attach = recv_frame(viewer)
            self.assertEqual(
                decode_screen_slot_message(attach, 1), SLOT_ATTACH
            )
            self.assertEqual(
                ScreenSourceDiscovery.from_dict(attach["source"]), source
            )

            unbound = decode_screen_server_status(
                _request(discovery, unbind_message(1))
            )
            self.assertEqual(unbound[0]["state"], "waiting")
            detach = recv_frame(viewer)
            self.assertEqual(
                decode_screen_slot_message(detach, 1), SLOT_DETACH
            )

            viewer.close()
            deadline = time.monotonic() + 2
            state = "waiting"
            while time.monotonic() < deadline:
                state = decode_screen_server_status(
                    _request(discovery, probe_message())
                )[0]["state"]
                if state == "closed":
                    break
                time.sleep(0.02)
            self.assertEqual(state, "closed")

            server.request_stop()
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            self.assertFalse(errors)


if __name__ == "__main__":
    unittest.main()
