from __future__ import annotations

import ast
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from world.catalog import MapCatalog, WORLD_ID
from world.contracts import ContractError
from world.navigation import (
    ActorBinding,
    NavigationService,
)
from world.navigation_store import NavigationStore


ROOT = Path(__file__).resolve().parents[2]


class FakeWorld:
    def __init__(self):
        self.entity_id = "entity.yuki"
        self.world_id = WORLD_ID
        self.epoch = "epoch.1"
        self.tick = 0
        self.revision = 0
        self.zone = "hallway"
        self.x = 0.0
        self.vx = 0.0
        self.effort = 0.0
        self.receipts = []
        self.controller = None
        self.blocked = False

    def observe(self):
        self.tick += 1
        self.revision += 1
        if self.controller is not None:
            self.controller.advance()
        return {
            "entity_id": self.entity_id,
            "world_id": self.world_id,
            "world_epoch": self.epoch,
            "tick": self.tick,
            "world_revision": self.revision,
            "zone_id": self.zone,
            "physical": {"x": self.x, "vx": self.vx, "effort": self.effort},
            "physics_hz": 120,
            "receipts": list(self.receipts),
        }

    def transfer(self, source, target, portal_id, target_x):
        self.zone = target
        self.x = target_x
        self.vx = 0.0
        self.effort = 0.0
        if self.controller is not None and self.controller.record is not None:
            if self.controller.record.get("stop_on_zone_change"):
                self.controller.record["status"] = "transferred"
        self.receipts.append({
            "action_id": f"transfer.{len(self.receipts)+1}",
            "request_id": f"physics.{self.tick}",
            "status": "applied",
            "reason_code": "ok",
            "source_zone": source,
            "target_zone": target,
            "entity_id": self.entity_id,
            "world_epoch": self.epoch,
            "tick": self.tick,
            "world_revision": self.revision,
            "job_revision": 1,
            "observed_outcome": {"portal_id": portal_id},
        })


class FakeController:
    def __init__(self, world, *, blocked=False):
        self.world = world
        self.world.controller = self
        self.blocked = blocked
        self.record = None
        self.begin_calls = []
        self.cancel_calls = []
        self.serial = 0

    def available(self):
        return self.record is None or self.record["status"] in {
            "reached", "transferred", "cancelled", "failed", "blocked"
        }

    def begin(self, target_x, *, tolerance, max_seconds, stop_on_zone_change=False):
        if not self.available():
            raise RuntimeError("busy")
        self.serial += 1
        self.record = {
            "action_id": f"body.{self.serial}",
            "status": "running",
            "target_x": float(target_x),
            "tolerance": float(tolerance),
            "stop_on_zone_change": bool(stop_on_zone_change),
        }
        self.begin_calls.append(float(target_x))
        return dict(self.record)

    def status(self):
        return None if self.record is None else dict(self.record)

    def cancel(self, action_id):
        self.cancel_calls.append(action_id)
        if self.record is not None and self.record["action_id"] == action_id:
            self.record["status"] = "cancelled"
            self.world.effort = 0.0
            return {"accepted": True, "status": "cancel_requested"}
        return {"accepted": False, "status": "missing"}

    def advance(self):
        if self.record is None or self.record["status"] != "running":
            return
        if self.blocked or self.world.blocked:
            self.record["status"] = "blocked"
            self.world.vx = 0.0
            self.world.effort = 0.0
            return
        target = self.record["target_x"]
        delta = target - self.world.x
        step = max(-30.0, min(30.0, delta))
        previous = self.world.x
        self.world.x += step
        self.world.vx = step * 120.0
        self.world.effort = 1.0 if step > 0 else (-1.0 if step < 0 else 0.0)

        if self.world.zone == "hallway" and min(previous, self.world.x) <= 500 <= max(previous, self.world.x):
            self.world.transfer("hallway", "laboratory", "portal.hallway.laboratory", 1.0)
            return
        if self.world.zone == "laboratory":
            if min(previous, self.world.x) <= 900 <= max(previous, self.world.x):
                self.world.transfer("laboratory", "training/flat_run", "portal.laboratory.training", 1.0)
                return
            if min(previous, self.world.x) <= 0.5 and max(previous, self.world.x) >= 0:
                self.world.transfer("laboratory", "hallway", "portal.laboratory.hallway", 499.0)
                return
        if self.world.zone == "training/flat_run" and min(previous, self.world.x) <= 0.5 and max(previous, self.world.x) >= 0:
            self.world.transfer("training/flat_run", "laboratory", "portal.training.laboratory", 899.0)
            return

        if abs(self.world.x - target) <= self.record["tolerance"]:
            self.world.x = target
            self.world.vx = 0.0
            self.world.effort = 0.0
            self.record["status"] = "reached"


def service(world=None, controller=True, path=":memory:"):
    world = world or FakeWorld()
    control = FakeController(world) if controller is True else controller
    nav = NavigationService(
        actor=ActorBinding("character.yuki", "embodiment.yuki.primary", "entity.yuki", WORLD_ID),
        world=world,
        controller=control,
        store=NavigationStore(path),
        route_deadline_ticks=1000,
        poll_seconds=0.0005,
    )
    return nav, world, control


def wait_terminal(nav, action_id, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = nav.action_status(action_id)
        if status["status"] in {"arrived", "cancelled", "blocked", "failed", "uncertain"}:
            return status
        time.sleep(0.001)
    raise AssertionError(nav.action_status(action_id))


class NavigationLifecycleTests(unittest.TestCase):
    def test_full_route_round_trip_uses_one_entity_and_physical_portals(self):
        nav, world, controller = service()
        first = nav.navigate("training/flat_run", "request.to-training")
        done = wait_terminal(nav, first["action_id"])
        self.assertEqual(done["status"], "arrived")
        self.assertEqual(world.zone, "training/flat_run")
        self.assertEqual(world.entity_id, "entity.yuki")
        self.assertEqual(controller.begin_calls[:2], [500.0, 900.0])
        self.assertEqual(
            [item["target_zone"] for item in done["receipt"]["observed_outcome"]["route_receipts"]],
            ["laboratory", "training/flat_run"],
        )

        back = nav.navigate("hallway", "request.to-hallway")
        back_done = wait_terminal(nav, back["action_id"])
        self.assertEqual(back_done["status"], "arrived")
        self.assertEqual(world.zone, "hallway")
        self.assertEqual(world.entity_id, "entity.yuki")
        self.assertEqual(controller.begin_calls[-2:], [0.25, 0.25])

    def test_request_id_is_idempotent_and_conflicting_reuse_is_rejected(self):
        nav, _world, _controller = service()
        first = nav.navigate("laboratory", "request.same")
        same = nav.navigate("laboratory", "request.same")
        self.assertEqual(first["action_id"], same["action_id"])
        wait_terminal(nav, first["action_id"])
        with self.assertRaises(ContractError) as caught:
            nav.navigate("training/flat_run", "request.same")
        self.assertEqual(caught.exception.code, "request_conflict")

    def test_missing_skill_and_foreign_entity_do_not_bypass_boundary(self):
        nav, world, _ = service(controller=None)
        action = nav.navigate("laboratory", "request.no-skill")
        done = nav.action_status(action["action_id"])
        self.assertEqual(done["status"], "failed")
        self.assertEqual(done["receipt"]["reason_code"], "skill_missing")

        world.entity_id = "entity.other"
        with self.assertRaises(ContractError) as caught:
            nav.observe()
        self.assertEqual(caught.exception.code, "identity_mismatch")

    def test_blocked_path_never_falls_back_to_scripted_motion(self):
        world = FakeWorld()
        controller = FakeController(world, blocked=True)
        nav, _, _ = service(world, controller)
        action = nav.navigate("laboratory", "request.blocked")
        done = wait_terminal(nav, action["action_id"])
        self.assertEqual(done["status"], "blocked")
        self.assertEqual(done["receipt"]["reason_code"], "blocked")
        source = (ROOT / "world" / "navigation.py").read_text(encoding="utf-8")
        self.assertNotIn("motor_x", source)
        self.assertNotIn("set_position", source)
        self.assertNotIn("submit_input", source)

    def test_stale_epoch_is_uncertain_not_retried(self):
        nav, world, controller = service()
        action = nav.navigate("laboratory", "request.epoch")
        world.epoch = "epoch.2"
        done = wait_terminal(nav, action["action_id"])
        self.assertEqual(done["status"], "uncertain")
        self.assertEqual(done["receipt"]["reason_code"], "stale_world")
        self.assertLessEqual(len(controller.begin_calls), 1)

    def test_cancel_is_idempotent_and_new_goal_waits_for_terminal_order(self):
        world = FakeWorld()
        controller = FakeController(world)
        controller.advance = lambda: None
        nav, _, _ = service(world, controller)
        action = nav.navigate("laboratory", "request.long")
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and not controller.begin_calls:
            nav.action_status(action["action_id"])
            time.sleep(0.001)
        with self.assertRaises(ContractError) as caught:
            nav.navigate("training/flat_run", "request.concurrent")
        self.assertEqual(caught.exception.code, "busy")

        cancel = nav.action_cancel(action["action_id"], "cancel.1")
        again = nav.action_cancel(action["action_id"], "cancel.1")
        self.assertEqual(cancel, again)
        self.assertIn("control_released", cancel)
        done = wait_terminal(nav, action["action_id"])
        self.assertEqual(done["status"], "cancelled")

        controller.advance = FakeController.advance.__get__(controller, FakeController)
        next_action = nav.navigate("laboratory", "request.after-cancel")
        self.assertNotEqual(next_action["action_id"], action["action_id"])

    def test_interaction_requires_observed_proximity_and_has_own_receipt(self):
        nav, world, _controller = service()
        world.zone = "laboratory"
        world.x = 450
        far = nav.interact("workstation", "work", "request.work.far")
        self.assertEqual(far["status"], "blocked")
        world.x = 500
        near = nav.interact("workstation", "work", "request.work.near")
        self.assertEqual(near["status"], "arrived")
        self.assertTrue(near["receipt"]["observed_outcome"]["interaction_confirmed"])

    def test_restart_reconciles_receipt_without_replaying_controller_goal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "navigation.sqlite3")
            store = NavigationStore(path)
            route = [{
                "source_zone": "hallway",
                "target_zone": "laboratory",
                "portal_id": "portal.hallway.laboratory",
            }]
            record, _ = store.reserve(
                request_id="request.crash",
                kind="navigate",
                target_id="laboratory",
                entity_id="entity.yuki",
                source_zone="hallway",
                target_zone="laboratory",
                world_epoch="epoch.1",
                observed_tick=10,
                route=route,
                detail={"route_receipts": [{
                    "action_id": "transfer.1",
                    "request_id": "physics.11",
                    "status": "applied",
                    "reason_code": "ok",
                    "source_zone": "hallway",
                    "target_zone": "laboratory",
                    "entity_id": "entity.yuki",
                    "world_epoch": "epoch.1",
                    "tick": 11,
                    "world_revision": 11,
                    "job_revision": 1,
                    "observed_outcome": {"portal_id": "portal.hallway.laboratory"},
                }]},
            )
            store.update(record["action_id"], status="transfer_pending", observed_tick=11)
            store.close()

            world = FakeWorld()
            world.tick = 11
            world.revision = 11
            world.zone = "laboratory"
            world.x = 1.0
            controller = FakeController(world)
            nav = NavigationService(
                actor=ActorBinding(
                    "character.yuki", "embodiment.yuki.primary", "entity.yuki", WORLD_ID
                ),
                world=world,
                controller=controller,
                store=NavigationStore(path),
                poll_seconds=0.0005,
            )
            reconciled = nav.action_status(record["action_id"])
            self.assertEqual(reconciled["status"], "arrived")
            self.assertTrue(reconciled["receipt"]["observed_outcome"]["reconciled"])
            duplicate = nav.navigate("laboratory", "request.crash")
            self.assertEqual(duplicate["action_id"], record["action_id"])
            self.assertEqual(controller.begin_calls, [])


class NavigationMcpContractTests(unittest.TestCase):
    def test_schema_and_mcp_surface_are_exact_and_do_not_accept_actor_or_actuator_fields(self):
        schema = json.loads((ROOT / "world" / "navigation_v1.schema.json").read_text())
        expected = {
            "describe": [],
            "observe": [],
            "locations": [],
            "navigate": ["location_id", "request_id"],
            "approach": ["object_id", "request_id"],
            "interact": ["object_id", "interaction_id", "request_id"],
            "action_status": ["action_id"],
            "action_cancel": ["action_id", "request_id"],
        }
        self.assertEqual(
            {tool["name"]: tool["arguments"] for tool in schema["tools"]},
            expected,
        )
        tree = ast.parse((ROOT / "world" / "mcp.py").read_text(encoding="utf-8"))
        decorated = {}
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if any(
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and dec.func.attr == "tool"
                for dec in node.decorator_list
            ):
                decorated[node.name] = [arg.arg for arg in node.args.args]
        self.assertEqual(decorated, expected)
        forbidden = {"entity_id", "motor_x", "x", "vx", "controller_id"}
        self.assertTrue(all(not (forbidden & set(args)) for args in decorated.values()))
        self.assertNotIn("reset", decorated)
        self.assertNotIn("move", decorated)
        self.assertNotIn("transfer", decorated)


if __name__ == "__main__":
    unittest.main()
