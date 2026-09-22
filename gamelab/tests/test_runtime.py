from __future__ import annotations

import unittest

from gamelab.host import HostError
from gamelab.runtime import ensure_player


class _HubClient:
    def __init__(self, session: dict | None) -> None:
        self._session = session
        self.login_calls = 0
        self.logout_calls = 0

    def session(self):
        if self._session is None:
            raise HostError("GameClient Host is not logged in")
        return dict(self._session)

    def state(self):
        if self._session is None:
            raise HostError("GameClient Host is not logged in")
        return {
            "session": dict(self._session),
            "snapshot": {
                "entities": [
                    {
                        "entity_id": self._session["entity_id"],
                        "x": 321.0,
                        "vx": 0.0,
                        "move_x": 0,
                    }
                ]
            },
        }

    def login(self, player_id: str):
        self.login_calls += 1
        raise AssertionError("GameLab must not login")

    def logout(self):
        self.logout_calls += 1
        raise AssertionError("GameLab must not logout")


class RuntimeHubTests(unittest.TestCase):
    def test_attach_to_existing_host_session(self):
        client = _HubClient({
            "player_id": "player1",
            "entity_id": "actor-player1",
            "world_id": "world1",
            "zone_id": "zone1",
            "sequence": 7,
        })
        state = ensure_player(client, "player1")
        self.assertEqual(state["snapshot"]["entities"][0]["x"], 321.0)
        self.assertEqual(client.login_calls, 0)
        self.assertEqual(client.logout_calls, 0)

    def test_missing_host_session_is_not_created_by_gamelab(self):
        client = _HubClient(None)
        with self.assertRaisesRegex(HostError, "login through the game client"):
            ensure_player(client, "player1")
        self.assertEqual(client.login_calls, 0)
        self.assertEqual(client.logout_calls, 0)

    def test_wrong_active_player_is_not_replaced(self):
        client = _HubClient({
            "player_id": "player2",
            "entity_id": "actor-player2",
            "world_id": "world1",
            "zone_id": "zone1",
            "sequence": 3,
        })
        with self.assertRaisesRegex(HostError, "owns player2"):
            ensure_player(client, "player1")
        self.assertEqual(client.login_calls, 0)
        self.assertEqual(client.logout_calls, 0)


if __name__ == "__main__":
    unittest.main()
