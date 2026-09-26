from __future__ import annotations

import unittest

from organism.runtime import reset_player_state


class FakeEmbodiedClient:
    def __init__(self):
        self.generation = 3
        self.tick = 10
        self.x = 500.0
        self.reset_calls = []

    def _state(self):
        return {
            "session": {
                "session_id": "session.1",
                "player_id": "player1",
                "entity_id": "entity.yuki",
                "zone_id": "training/flat_run",
                "sequence": 7,
                "controller_generation": self.generation,
            },
            "snapshot": {
                "world_epoch": "epoch.1",
                "world_tick": self.tick,
                "entities": [{
                    "entity_id": "entity.yuki",
                    "x": self.x,
                    "vx": 0.0,
                    "motor_x": 0.0,
                }],
            },
            "observation": {
                "world_epoch": "epoch.1",
                "tick": self.tick,
                "zone_id": "training/flat_run",
                "physical": {
                    "x": self.x,
                    "vx": 0.0,
                    "effort": 0.0,
                },
            },
            "controller": {
                "generation": self.generation,
            },
        }

    def session(self):
        return self._state()["session"]

    def state(self):
        return self._state()

    def training_reset(self, x):
        self.reset_calls.append(x)
        self.x = float(x)
        self.generation += 1
        self.tick += 1
        return {
            "event": {
                "command_id": "action.reset.1",
            }
        }


class EmbodiedEpisodeResetTests(unittest.TestCase):
    def test_authoritative_observation_and_fence_bump_confirm_reset(self):
        client = FakeEmbodiedClient()
        state = reset_player_state(
            client,
            "player1",
            spawn_x=731.0,
        )
        self.assertEqual(client.reset_calls, [731.0])
        self.assertEqual(state["observation"]["physical"]["x"], 731.0)
        self.assertEqual(state["session"]["controller_generation"], 4)
        self.assertEqual(state["session"]["sequence"], 7)


if __name__ == "__main__":
    unittest.main()
