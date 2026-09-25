from __future__ import annotations

import copy
import json
import math
import unittest

from world.catalog import MAP_FILES, MapCatalog, MapManifest, WORLD_ID
from world.contracts import ContractError


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.catalog = MapCatalog.load_default()

    def test_catalog_has_exact_three_world_zones_and_no_workstation_zone(self):
        self.assertEqual(
            self.catalog.map_ids(),
            ("hallway", "laboratory", "training/flat_run"),
        )
        self.assertNotIn("laboratory.workstation", self.catalog.map_ids())
        workstation = [
            item for item in self.catalog.semantics("laboratory")["objects"]
            if item["object_id"] == "workstation"
        ]
        self.assertEqual(len(workstation), 1)
        self.assertIn("work", workstation[0]["interactions"])

    def test_hallway_flat_contract_matches_intro_geometry(self):
        physics = self.catalog.physics("hallway")
        presentation = self.catalog.presentation("hallway")
        self.assertEqual(physics["profile"], "flat_1d")
        self.assertEqual(physics["bounds"], {"x_min": 0.0, "x_max": 1000.0})
        self.assertEqual(presentation["viewport"], {"axis": "x", "cells": 1001})
        spawns = {item["spawn_id"]: item["x"] for item in physics["spawns"]}
        self.assertEqual(spawns["yuki_day_start"], 0.0)
        self.assertEqual(spawns["director_first_day"], 1.0)
        portal = physics["portals"][0]
        self.assertEqual(portal["activation"], "on_touch")
        self.assertLessEqual(portal["trigger"]["x_min"], 500.0)
        self.assertGreaterEqual(portal["trigger"]["x_max"], 500.0)

    def test_flat_maps_publish_explicit_blocked_intervals(self):
        for map_id in self.catalog.map_ids():
            physics = self.catalog.physics(map_id)
            self.assertIn("blocked", physics)
            self.assertEqual(physics["blocked"], [])

    def test_graph_links_and_target_spawns_are_valid(self):
        edges = set()
        for source in self.catalog.map_ids():
            for portal in self.catalog.physics(source)["portals"]:
                edges.add((source, portal["target_map_id"]))
        self.assertEqual(edges, {
            ("hallway", "laboratory"),
            ("laboratory", "hallway"),
            ("laboratory", "training/flat_run"),
            ("training/flat_run", "laboratory"),
        })

    def test_consumer_views_do_not_leak_other_manifest_sections(self):
        physics = self.catalog.physics("hallway")
        semantics = self.catalog.semantics("hallway")
        presentation = self.catalog.presentation("hallway")
        self.assertNotIn("semantics", physics)
        self.assertNotIn("presentation", physics)
        self.assertNotIn("physics", semantics)
        self.assertNotIn("physics", presentation)
        physics["bounds"]["x_max"] = 1
        self.assertEqual(self.catalog.physics("hallway")["bounds"]["x_max"], 1000.0)

    def test_unknown_map_is_rejected(self):
        with self.assertRaises(ContractError) as caught:
            self.catalog.get("../../secret")
        self.assertEqual(caught.exception.code, "unknown_map")

    def test_unknown_version_and_incompatible_profile_are_rejected(self):
        base = self.catalog.get("hallway").to_dict()
        unknown = copy.deepcopy(base)
        unknown["schema_version"] = 2
        with self.assertRaises(ContractError) as caught:
            MapManifest.from_dict(unknown)
        self.assertEqual(caught.exception.code, "unsupported_version")

        incompatible = copy.deepcopy(base)
        incompatible["physics"]["profile"] = "flat_2d"
        with self.assertRaises(ContractError) as caught:
            MapManifest.from_dict(incompatible)
        self.assertEqual(caught.exception.code, "unsupported_profile")

        gravity = copy.deepcopy(base)
        gravity["physics"]["gravity"] = 9.81
        with self.assertRaises(ContractError) as caught:
            MapManifest.from_dict(gravity)
        self.assertEqual(caught.exception.code, "unsupported_profile")

        dimension = copy.deepcopy(base)
        dimension["physics"]["dimensions"] = ["x", "y"]
        with self.assertRaises(ContractError) as caught:
            MapManifest.from_dict(dimension)
        self.assertEqual(caught.exception.code, "unsupported_profile")

    def test_manifest_rejects_nan_and_extra_y_axis(self):
        base = self.catalog.get("hallway").to_dict()
        bad = copy.deepcopy(base)
        bad["physics"]["bounds"]["x_max"] = math.nan
        with self.assertRaises(ValueError):
            MapManifest.from_dict(bad)

        bad = copy.deepcopy(base)
        bad["physics"]["bounds"]["y_min"] = 0
        with self.assertRaises(ValueError):
            MapManifest.from_dict(bad)

    def test_catalog_world_id_is_stable(self):
        self.assertEqual(WORLD_ID, "yuki-world-v1")
        for map_id in MAP_FILES:
            self.assertEqual(self.catalog.get(map_id).world_id, WORLD_ID)


if __name__ == "__main__":
    unittest.main()
