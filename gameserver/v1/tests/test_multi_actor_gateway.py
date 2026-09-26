from __future__ import annotations

import unittest

from gameserver.v1.common.protocol import message
from gameserver.v1.gateway.embodied import EmbodiedGatewayService
from gameserver.v1.world.embodied_server import EmbodiedWorldService


class MultiActorGatewayTests(unittest.TestCase):
    def test_yuki_and_director_have_distinct_persistent_bindings(self):
        world = EmbodiedWorldService(port=0, physics_hz=240)
        world.start()
        gateway = EmbodiedGatewayService(port=0, world_port=world.address[1])
        try:
            players = gateway.dispatch(message("list_players"))["players"]
            self.assertEqual(set(players), {"player1", "director1"})
            yuki = gateway.dispatch(message("login", player_id="player1"))
            director = gateway.dispatch(message("login", player_id="director1"))
            self.assertEqual(yuki["entity_id"], "entity.yuki")
            self.assertEqual(director["entity_id"], "entity.director")
            ys = gateway.dispatch(message("snapshot", session_id=yuki["session_id"]))
            ds = gateway.dispatch(message("snapshot", session_id=director["session_id"]))
            self.assertEqual(ys["observation"]["physical"]["x"], 0.0)
            self.assertEqual(ds["observation"]["physical"]["x"], 1.0)
            self.assertEqual(
                ys["observation"]["world_epoch"], ds["observation"]["world_epoch"]
            )
            self.assertEqual(ys["freshness"]["source"], "world_state_hub")
            self.assertEqual(ds["freshness"]["source"], "world_state_hub")
            before = gateway.state_hub.status()["polls"]
            for _ in range(100):
                gateway.dispatch(message("snapshot", session_id=yuki["session_id"]))
                gateway.dispatch(message("snapshot", session_id=director["session_id"]))
            after = gateway.state_hub.status()["polls"]
            self.assertLess(after - before, 10)
            with self.assertRaises(Exception):
                gateway.dispatch(message(
                    "training_reset", session_id=director["session_id"], x=500.0
                ))
        finally:
            gateway.close()
            world.shutdown()


if __name__ == "__main__":
    unittest.main()
