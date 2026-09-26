from __future__ import annotations
import json
from pathlib import Path
import unittest
from graphics.assets import public_asset_catalog
from graphics.contracts import MAX_FRAME_BYTES
from graphics.projector import SceneProjector
from graphics.live import EmbodiedWorldGraphics
from organism.host import HostError
from graphics.stream import FrameHub
from world.catalog import WORLD_ID

def entity(entity_id, owner_id, x, *, zone="hallway", vx=0.0):
    return {"entity_id":entity_id,"embodiment_id":"emb."+entity_id,"owner_id":owner_id,
            "kind":"character","zone_id":zone,"x":x,"vx":vx,"motor_x":0.0,
            "controller_id":"controller."+entity_id,"controller_generation":1,
            "body_profile_hash":"a"*64,"last_sequence":0,"last_input_tick":0,"control_state":"ready"}

def snapshot(*entities, tick=10, revision=10, epoch="epoch.1", previous=None):
    return {"world_id":WORLD_ID,"world_epoch":epoch,"previous_epoch":previous,
            "world_tick":tick,"world_revision":revision,"entities":list(entities)}

class ProjectionTests(unittest.TestCase):
    def setUp(self): self.projector = SceneProjector()
    def test_hallway_static_and_occupancy_layers_have_1001_cells(self):
        terrain = self.projector.terrain("hallway")
        frame = self.projector.project(snapshot(
            entity("entity.yuki","character.yuki",0.0),
            entity("entity.director","character.director",1.0)),
            focus_entity_id="entity.yuki")
        layers = self.projector.layers(frame, terrain)
        self.assertEqual(len(layers["terrain"]),1001); self.assertEqual(len(layers["occupancy"]),1001)
        self.assertEqual(layers["terrain"][0],"@"); self.assertEqual(layers["terrain"][500],"%")
        self.assertEqual(layers["terrain"][1000],"!")
        self.assertEqual(layers["occupancy"][0],["entity.yuki"]); self.assertEqual(layers["occupancy"][1],["entity.director"])
        self.assertEqual(layers["terrain"][0],"@")
    def test_floor_quantization_and_two_actors_can_share_one_display_cell(self):
        frame = self.projector.project(snapshot(
            entity("entity.yuki","character.yuki",10.2),
            entity("entity.director","character.director",10.9)),focus_entity_id="entity.yuki")
        layers = self.projector.layers(frame,self.projector.terrain("hallway"))
        self.assertEqual(layers["occupancy"][10],["entity.yuki","entity.director"])
        rendered={item["entity_id"]:item for item in frame["entities"]}
        self.assertEqual(rendered["entity.yuki"]["transform"]["x"],10.2)
        self.assertEqual(rendered["entity.director"]["transform"]["x"],10.9)
    def test_pose_comes_from_interaction_not_location(self):
        yuki=entity("entity.yuki","character.yuki",500.0,zone="laboratory")
        plain=self.projector.project(snapshot(yuki),focus_entity_id="entity.yuki")
        self.assertEqual(plain["entities"][0]["animation_state"],"idle")
        seated=self.projector.project(snapshot(yuki),focus_entity_id="entity.yuki",
            interactions=[{"entity_id":"entity.yuki","object_id":"workstation","interaction_id":"sit","status":"active"}])
        self.assertEqual(seated["entities"][0]["animation_state"],"seated_working")
    def test_frame_zone_and_revision_come_only_from_one_world_snapshot(self):
        yuki=entity("entity.yuki","character.yuki",1.0,zone="laboratory")
        director=entity("entity.director","character.director",499.0,zone="hallway")
        frame=self.projector.project(snapshot(yuki,director,tick=44,revision=77),focus_entity_id="entity.yuki")
        self.assertEqual(frame["zone_id"],"laboratory"); self.assertEqual(frame["source_world_tick"],44)
        self.assertEqual(frame["source_world_revision"],77)
        self.assertEqual([item["entity_id"] for item in frame["entities"]],["entity.yuki"])
        self.assertLess(len(json.dumps(frame).encode()),MAX_FRAME_BYTES)
    def test_asset_catalog_keeps_placeholders_replaceable(self):
        assets=public_asset_catalog()["assets"]
        self.assertEqual(assets["marker.yuki"]["glyph"],"P"); self.assertEqual(assets["marker.director"]["glyph"],"D")
        self.assertIn("background.training_flat_run",assets)

class FrameHubTests(unittest.TestCase):
    def _frame(self,revision,*,epoch="epoch.1",previous=None):
        return SceneProjector().project(
            snapshot(entity("entity.yuki","character.yuki",float(revision)),tick=revision,revision=revision,epoch=epoch,previous=previous),
            focus_entity_id="entity.yuki")
    def test_slow_tabs_get_latest_frame_without_backpressuring_each_other(self):
        hub=FrameHub(limit=3); first=hub.publish(self._frame(1)); hub.publish(self._frame(2)); latest=hub.publish(self._frame(3))
        self.assertEqual(hub.since(0),[latest]); self.assertEqual(hub.since(first["id"]),[latest])
        self.assertEqual(hub.since(0)[0]["data"]["frame"]["source_world_revision"],3)
        self.assertLessEqual(len(hub.events),3)
    def test_reorder_and_old_epoch_frames_are_rejected(self):
        hub=FrameHub(); current=hub.publish(self._frame(5)); self.assertIsNone(hub.publish(self._frame(4)))
        reset=hub.publish(self._frame(1,epoch="epoch.2",previous="epoch.1")); self.assertTrue(reset["data"]["reset"])
        self.assertIsNone(hub.publish(self._frame(6,epoch="epoch.1")))
        self.assertEqual(hub.since(current["id"])[0]["data"]["frame"]["source_world_epoch"],"epoch.2")

class BrowserBoundaryTests(unittest.TestCase):
    def test_browser_renderer_has_no_zone_or_portal_decision_rules(self):
        root=Path(__file__).resolve().parents[2]/"gametable"/"web"/"js"
        renderer=(root/"frame-renderer.js").read_text(encoding="utf-8")
        for forbidden in ("laboratory","hallway","training/flat_run","portal."):
            self.assertNotIn(forbidden,renderer)
        self.assertIn("frame.zone_id",renderer); self.assertIn("requestAnimationFrame",renderer)


class _FakeGraphicsClient:
    def state(self):
        world = snapshot(
            entity("entity.yuki", "character.yuki", 42.5),
            tick=77, revision=81, epoch="epoch.live",
        )
        return {
            "session": {
                "entity_id": "entity.yuki",
                "player_id": "player1",
                "zone_id": "hallway",
            },
            "snapshot": world,
            "observation": {
                "entity_id": "entity.yuki",
                "world_id": WORLD_ID,
                "world_epoch": "epoch.live",
                "tick": 77,
                "world_revision": 81,
                "zone_id": "hallway",
                "physical": {"x": 42.5, "vx": 0.0, "effort": 0.0},
            },
        }
    def close(self):
        pass


class _FlakyGraphicsClient(_FakeGraphicsClient):
    def __init__(self):
        self.calls = 0
    def state(self):
        self.calls += 1
        if self.calls == 1:
            raise HostError("temporary snapshot timeout")
        return super().state()


class LiveGraphicsTests(unittest.TestCase):
    def test_read_only_snapshot_retries_one_transient_host_failure(self):
        client = _FlakyGraphicsClient()
        graphics = EmbodiedWorldGraphics(client=client)
        value = graphics.snapshot({})
        self.assertEqual(client.calls, 2)
        self.assertEqual(value["observation"]["observed_tick"], 77)

    def test_embodied_source_is_authoritative_and_exports_same_observation(self):
        graphics = EmbodiedWorldGraphics(client=_FakeGraphicsClient())
        value = graphics.snapshot({})
        self.assertTrue(value["authoritative"])
        self.assertTrue(value["cutover_ready"])
        self.assertEqual(
            value["frame"]["freshness"]["source"], "embodied_world_v1"
        )
        self.assertTrue(value["frame"]["freshness"]["authoritative"])
        self.assertEqual(value["observation"]["location_id"], "hallway")
        self.assertEqual(value["observation"]["observed_tick"], 77)


if __name__ == "__main__":
    unittest.main()
