"""Static map presentation derived only from versioned map manifests."""
from __future__ import annotations
from world.catalog import MapCatalog
from .assets import object_asset
from .camera import flat_camera, quantize_x

def terrain_layer(catalog: MapCatalog, map_id: str) -> dict:
    manifest = catalog.get(map_id)
    physics, semantics = catalog.physics(map_id), catalog.semantics(map_id)
    presentation = catalog.presentation(map_id)
    camera = flat_camera(physics)
    cells = ["."] * camera["cells"]
    cells[-1] = "!"
    objects = []
    for item in semantics["objects"]:
        cell = quantize_x(item["x"], camera)
        glyph = {"exit_marker":"@","portal":"%","workstation":"W","training_area":"T"}.get(item["kind"],"?")
        cells[cell] = glyph
        objects.append({
            "object_id":item["object_id"], "kind":item["kind"], "x":float(item["x"]),
            "display_cell":cell, "visual_asset":object_asset(item["kind"]),
        })
    return {
        "map_id":map_id, "map_version":manifest.map_version,
        "terrain_revision":presentation["terrain_revision"],
        "theme_id":presentation["theme_id"],
        "background_asset":"background."+presentation["theme_id"],
        "camera":camera, "base_asset":"terrain.road", "cells":cells, "objects":objects,
    }

__all__ = ["terrain_layer"]
