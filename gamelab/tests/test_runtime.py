from __future__ import annotations

import unittest

from gamelab.host import HostError
from gamelab.runtime import reset_player


class _ResetClient:
    def __init__(self, *, transient_failures: int = 1, fatal: str | None = None) -> None:
        self.transient_failures = transient_failures
        self.fatal = fatal
        self.login_calls = 0
        self.logged_in = False

    def logout(self):
        self.logged_in = False
        return {}

    def login(self, player_id: str):
        self.login_calls += 1
        if self.fatal is not None:
            raise HostError(self.fatal)
        if self.login_calls <= self.transient_failures:
            raise HostError("entity already exists")
        self.logged_in = True
        return {}

    def state(self):
        if not self.logged_in:
            raise HostError("not logged in")
        return {
            "session": {
                "player_id": "player1",
                "entity_id": "actor-player1",
            },
            "snapshot": {
                "entities": [
                    {
                        "entity_id": "actor-player1",
                        "x": 100.0,
                        "vx": 0.0,
                        "move_x": 0,
                    }
                ]
            },
        }


class RuntimeResetTests(unittest.TestCase):
    def test_reset_retries_zone_despawn_race(self):
        client = _ResetClient(transient_failures=2)
        state = reset_player(client, "player1", timeout=0.5)
        self.assertEqual(client.login_calls, 3)
        self.assertEqual(state["snapshot"]["entities"][0]["x"], 100.0)

    def test_reset_does_not_hide_unrelated_login_errors(self):
        client = _ResetClient(transient_failures=0, fatal="unknown player_id")
        with self.assertRaisesRegex(HostError, "unknown player_id"):
            reset_player(client, "player1", timeout=0.5)


if __name__ == "__main__":
    unittest.main()
