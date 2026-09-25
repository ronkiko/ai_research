"""Project one authoritative world snapshot into a browser RenderFrame."""
from __future__ import annotations
import copy
from typing import Any, Iterable
from world.catalog import MapCatalog
from .assets import entity_asset
from .camera import flat_camera, quantize_x, screen_x
from .contracts import SCHEMA_VERSION, validate_render_frame
from .presentation import terrain_layer

REST_VELOCITY = 0.05

class SceneProjector:
    def __init__(self, catalog: MapCatalog | None = None):
        self.catalog = catalog or MapCatalog.load_default()

    @staticmethod
    def _interaction_state(entity_id: str, interactions: Iterable[dict[str, Any]]) -> str | None:
        for item in interactions:
            if not isinstance(item, dict) or item.get("entity_id") != entity_id:
                continue
            if item.get("object_id") == "workstation" and item.get("interaction_id") in {"sit","work"} and item.get("status") in {"active","arrived","confirmed"}:
                return "seated_working"
        return None

    def project(self, snapshot: dict[str, Any], *, focus_entity_id: str,
                interactions: Iterable[dict[str, Any]] = (),
                authoritative: bool = True, source: str = "embodied_world") -> dict[str, Any]:
        required = {"world_id","world_epoch","world_tick","world_revision","entities"}
        if not isinstance(snapshot, dict) or not required <= set(snapshot):
            raise ValueError("world snapshot is incomplete")
        if type(snapshot["world_tick"]) is not int or snapshot["world_tick"] < 0:
            raise ValueError("world_tick is invalid")
        if type(snapshot["world_revision"]) is not int or snapshot["world_revision"] < 0:
            raise ValueError("world_revision is invalid")
        entities = snapshot["entities"]
        if not isinstance(entities, list):
            raise ValueError("entities must be a list")
        focus = next((item for item in entities if item.get("entity_id") == focus_entity_id), None)
        if focus is None:
            raise ValueError("focus entity is absent from world snapshot")
        zone_id = focus.get("zone_id")
        self.catalog.get(zone_id)
        camera = flat_camera(self.catalog.physics(zone_id))
        layer = terrain_layer(self.catalog, zone_id)
        rendered, seen = [], set()
        for entity in entities:
            if entity.get("zone_id") != zone_id:
                continue
            entity_id = entity.get("entity_id")
            if not isinstance(entity_id, str) or not entity_id or entity_id in seen:
                raise ValueError("snapshot entity IDs must be unique non-empty strings")
            seen.add(entity_id)
            x, vx = float(entity["x"]), float(entity.get("vx", 0.0))
            pose = self._interaction_state(entity_id, interactions)
            rendered.append({
                "entity_id":entity_id, "transform":{"x":x},
                "display_cell":quantize_x(x,camera), "screen_x":screen_x(x,camera),
                "visual_asset":entity_asset(entity),
                "animation_state":pose or ("moving" if abs(vx)>REST_VELOCITY else "idle"),
            })
        frame = {
            "schema_version":SCHEMA_VERSION,
            "frame_id":f"frame:{snapshot['world_epoch']}:{snapshot['world_revision']}:{zone_id}",
            "source_world_epoch":str(snapshot["world_epoch"]),
            "source_world_tick":snapshot["world_tick"],
            "source_world_revision":snapshot["world_revision"],
            "zone_id":zone_id, "camera":camera,
            "terrain_revision":layer["terrain_revision"],
            "entities":rendered, "props":copy.deepcopy(layer["objects"]),
            "presentation_revision":f"{zone_id}@{layer['map_version']}:{layer['theme_id']}",
            "freshness":{
                "state":"current","age_ticks":0,"authoritative":bool(authoritative),
                "source":source,"previous_epoch":snapshot.get("previous_epoch"),
            },
        }
        return validate_render_frame(frame)

    def terrain(self, zone_id: str) -> dict:
        return terrain_layer(self.catalog, zone_id)

    @staticmethod
    def layers(frame: dict[str, Any], terrain: dict[str, Any]) -> dict[str, Any]:
        validate_render_frame(frame)
        cells = list(terrain["cells"])
        if len(cells) != frame["camera"]["cells"]:
            raise ValueError("terrain and frame camera sizes differ")
        occupancy = [[] for _ in cells]
        for entity in frame["entities"]:
            occupancy[entity["display_cell"]].append(entity["entity_id"])
        return {"terrain":cells,"occupancy":occupancy}

__all__ = ["REST_VELOCITY","SceneProjector"]
