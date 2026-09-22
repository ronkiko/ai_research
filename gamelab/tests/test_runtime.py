from __future__ import annotations

import unittest

from gamelab.host import HostError
from gamelab.runtime import ensure_player, reset_player_state


class _HubClient:
    def __init__(self, session: dict | None) -> None:
        self._session = session
        self.login_calls = 0
        self.logout_calls = 0
        self.reset_calls = 0
        self.x = 321.0
        self.vx = 180.0
        self.move_x = 1

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
                        "x": self.x,
                        "vx": self.vx,
                        "move_x": self.move_x,
                    }
                ]
            },
        }

    def reset(self):
        self.reset_calls += 1
        self.x = 100.0
        self.vx = 0.0
        self.move_x = 0
        return {"sequence": self._session["sequence"], "x": 100.0}

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

    def test_episode_reset_preserves_host_session_sequence(self):
        client = _HubClient({
            "player_id": "player1",
            "entity_id": "actor-player1",
            "world_id": "world1",
            "zone_id": "zone1",
            "sequence": 7,
        })
        state = reset_player_state(client, "player1", timeout=0.5)
        player = state["snapshot"]["entities"][0]
        self.assertEqual(player["x"], 100.0)
        self.assertEqual(player["vx"], 0.0)
        self.assertEqual(player["move_x"], 0)
        self.assertEqual(state["session"]["sequence"], 7)
        self.assertEqual(client.reset_calls, 1)
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
