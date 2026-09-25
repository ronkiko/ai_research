"""Pre-cutover graphics adapter for the current VN save.

Frames from this adapter are explicitly non-authoritative. They let the stage-06
browser consume RenderFrame before stage 09 switches GameTable to the embodied world.
"""
from __future__ import annotations
from world.catalog import WORLD_ID
from .projector import SceneProjector

_SCENES = {
    "hallway":{
        "zone_id":"hallway",
        "entities":[
            {"entity_id":"entity.yuki","owner_id":"character.yuki","kind":"character","zone_id":"hallway","x":0.0,"vx":0.0,"motor_x":0.0},
            {"entity_id":"entity.director","owner_id":"character.director","kind":"character","zone_id":"hallway","x":1.0,"vx":0.0,"motor_x":0.0},
        ],
        "interactions":[],
    },
    "laboratory.workstation":{
        "zone_id":"laboratory",
        "entities":[
            {"entity_id":"entity.yuki","owner_id":"character.yuki","kind":"character","zone_id":"laboratory","x":500.0,"vx":0.0,"motor_x":0.0},
        ],
        "interactions":[{"entity_id":"entity.yuki","object_id":"workstation","interaction_id":"work","status":"active"}],
    },
}

class LegacyVNGraphics:
    mode = "legacy_vn_compat"
    def __init__(self, projector: SceneProjector | None = None):
        self.projector = projector or SceneProjector()
    def snapshot(self, state: dict) -> dict:
        spec = _SCENES.get(state.get("scene_id"))
        if spec is None:
            raise ValueError(f"no pre-cutover graphics mapping for {state.get('scene_id')!r}")
        revision = int(state["revision"])
        world = {
            "world_id":WORLD_ID,"world_epoch":"legacy-vn-compat","previous_epoch":None,
            "world_tick":revision,"world_revision":revision,
            "entities":[dict(item) for item in spec["entities"]],
        }
        frame = self.projector.project(
            world, focus_entity_id="entity.yuki", interactions=spec["interactions"],
            authoritative=False, source=self.mode,
        )
        return {
            "mode":self.mode,"authoritative":False,"cutover_ready":False,
            "frame":frame,"terrain":self.projector.terrain(spec["zone_id"]),
        }

__all__ = ["LegacyVNGraphics"]
