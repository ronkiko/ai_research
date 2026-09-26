from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import time
import unittest

from gameserver.v1.common.config import PHYSICS_CONTRACT_SHA256, PHYSICS_HZ, LINE
from gameserver.v1.common.protocol import ProtocolError, message
from gameserver.v1.physics.kernel import FlatProfile, MotionState, step_flat_1d
from gameserver.v1.world.embodied import (
    BODY_PROFILE_SHA256, EmbodiedWorldRuntime,
)
from gameserver.v1.world.embodied_server import EmbodiedWorldService
from gameserver.v1.gateway.embodied import EmbodiedGatewayService
from gameserver.v1.world.store import WorldCheckpointStore
from world.catalog import MapCatalog, MapManifest
from world.contracts import ContractError


def spawn_yuki(runtime: EmbodiedWorldRuntime) -> dict:
    queued = runtime.submit_spawn(
        request_id="spawn.yuki",
        entity_id="entity.yuki",
        embodiment_id="embodiment.yuki.primary",
        owner_id="character.yuki",
        kind="character",
        zone_id="hallway",
        spawn_id="yuki_day_start",
        controller_id="controller.yuki",
        controller_generation=1,
        body_profile_hash=BODY_PROFILE_SHA256,
    )
    runtime.tick()
    return runtime.receipt(queued["action_id"])


def yuki(runtime: EmbodiedWorldRuntime) -> dict:
    return next(item for item in runtime.latest_snapshot()["entities"]
                if item["entity_id"] == "entity.yuki")


def drive_to_laboratory(runtime: EmbodiedWorldRuntime) -> dict:
    spawn_yuki(runtime)
    queued = runtime.submit_input(
        request_id="input.walk.right",
        entity_id="entity.yuki",
        expected_zone_id="hallway",
        expected_world_epoch=runtime.epoch,
        controller_id="controller.yuki",
        controller_generation=1,
        sequence=1,
        motor_x=1.0,
    )
    runtime.tick()
    self_receipt = runtime.receipt(queued["action_id"])
    assert self_receipt["status"] == "applied"
    for _ in range(1000):
        snapshot = runtime.tick()
        entity = next(item for item in snapshot["entities"]
                      if item["entity_id"] == "entity.yuki")
        if entity["zone_id"] == "laboratory":
            return entity
    raise AssertionError("Yuki never touched hallway laboratory portal")


class PhysicsKernelTests(unittest.TestCase):
    def test_shared_kernel_matches_legacy_numeric_order(self):
        profile = FlatProfile(
            0.0, LINE.length, LINE.player_max_speed, LINE.player_max_acceleration,
            LINE.player_drag, 0.05, 0.02,
        )
        state = MotionState(100.0, 0.0, 0.0)
        legacy_x, legacy_vx = 100.0, 0.0
        motors = [1.0] * 80 + [0.0] * 200 + [-0.4] * 40
        dt = 1.0 / PHYSICS_HZ
        for motor in motors:
            state = MotionState(state.x, state.vx, motor)
            result = step_flat_1d(state, profile, dt)
            state = result.state

            acceleration = LINE.player_max_acceleration * motor - LINE.player_drag * legacy_vx
            legacy_vx += acceleration * dt
            legacy_vx = max(-LINE.player_max_speed, min(LINE.player_max_speed, legacy_vx))
            if abs(motor) <= 0.02 and abs(legacy_vx) <= 0.05:
                legacy_vx = 0.0
            next_x = legacy_x + legacy_vx * dt
            if next_x >= LINE.length:
                legacy_x = LINE.length
                if legacy_vx > 0:
                    legacy_vx = 0.0
            elif next_x <= 0:
                legacy_x = 0.0
                if legacy_vx < 0:
                    legacy_vx = 0.0
            else:
                legacy_x = next_x

            self.assertEqual(state.x, legacy_x)
            self.assertEqual(state.vx, legacy_vx)

    def test_blocked_interval_stops_point_actor(self):
        profile = FlatProfile(0, 1000, 180, 720, 4, 0.05, 0.02, ((100, 200),))
        result = step_flat_1d(MotionState(99.9, 100.0, 1.0), profile, 1 / 120)
        self.assertEqual(result.state.x, 100.0)
        self.assertEqual(result.state.vx, 0.0)
        self.assertEqual(result.collision, "blocked")
        again = step_flat_1d(MotionState(result.state.x, 0.0, 1.0), profile, 1 / 120)
        self.assertEqual(again.state.x, 100.0)
        self.assertEqual(again.state.vx, 0.0)


class _CountingCheckpointStore:
    def __init__(self):
        self.saves = []
    def load(self):
        return None
    def save(self, payload):
        self.saves.append(copy.deepcopy(payload))
    def close(self):
        pass


class EmbodiedWorldTests(unittest.TestCase):
    def test_continuous_input_does_not_force_sqlite_checkpoint(self):
        store = _CountingCheckpointStore()
        runtime = EmbodiedWorldRuntime(
            store=store,
            checkpoint_interval_ticks=1000,
            controller_watchdog_ticks=5000,
        )
        spawn_yuki(runtime)
        baseline = len(store.saves)
        current = yuki(runtime)
        queued = runtime.submit_input(
            request_id="input.no-force-checkpoint",
            entity_id="entity.yuki",
            expected_zone_id=current["zone_id"],
            expected_world_epoch=runtime.epoch,
            controller_id=current["controller_id"],
            controller_generation=current["controller_generation"],
            sequence=current["last_sequence"] + 1,
            motor_x=1.0,
        )
        runtime.tick()
        self.assertEqual(
            runtime.receipt(queued["action_id"])["status"], "applied"
        )
        self.assertEqual(len(store.saves), baseline)

    def test_world_starts_without_demo_mob_and_ticks_without_clients(self):
        runtime = EmbodiedWorldRuntime()
        self.assertEqual(runtime.latest_snapshot()["entities"], [])
        before = runtime.world_tick
        for _ in range(12):
            runtime.tick()
        self.assertEqual(runtime.world_tick, before + 12)

    def test_portal_is_physical_swept_transfer_with_fence_bump(self):
        runtime = EmbodiedWorldRuntime(controller_watchdog_ticks=5000)
        entity = drive_to_laboratory(runtime)
        self.assertEqual(entity["zone_id"], "laboratory")
        self.assertEqual(entity["x"], 1.0)
        self.assertEqual(entity["vx"], 0.0)
        self.assertEqual(entity["motor_x"], 0.0)
        self.assertEqual(entity["controller_generation"], 2)
        transfers = runtime.latest_snapshot()["transfers"]
        self.assertEqual(len(transfers), 1)
        receipt = transfers[0]["receipt"]
        self.assertEqual(receipt["source_zone"], "hallway")
        self.assertEqual(receipt["target_zone"], "laboratory")
        self.assertEqual(receipt["observed_outcome"]["portal_id"],
                         "portal.hallway.laboratory")

    def test_late_old_zone_effort_is_rejected_after_transfer(self):
        runtime = EmbodiedWorldRuntime(controller_watchdog_ticks=5000)
        drive_to_laboratory(runtime)
        queued = runtime.submit_input(
            request_id="input.stale",
            entity_id="entity.yuki",
            expected_zone_id="hallway",
            expected_world_epoch=runtime.epoch,
            controller_id="controller.yuki",
            controller_generation=1,
            sequence=2,
            motor_x=1.0,
        )
        runtime.tick()
        receipt = runtime.receipt(queued["action_id"])
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["reason_code"], "stale_world")
        entity = yuki(runtime)
        self.assertEqual(entity["zone_id"], "laboratory")
        self.assertEqual(entity["motor_x"], 0.0)

    def test_controller_watchdog_releases_effort_without_teleporting_or_snapping_rest(self):
        runtime = EmbodiedWorldRuntime(controller_watchdog_ticks=2)
        spawn_yuki(runtime)
        queued = runtime.submit_input(
            request_id="input.watchdog",
            entity_id="entity.yuki",
            expected_zone_id="hallway",
            expected_world_epoch=runtime.epoch,
            controller_id="controller.yuki",
            controller_generation=1,
            sequence=1,
            motor_x=1.0,
        )
        runtime.tick()
        self.assertEqual(runtime.receipt(queued["action_id"])["status"], "applied")
        runtime.tick()
        before_release = yuki(runtime)
        runtime.tick()
        released = yuki(runtime)
        self.assertEqual(released["motor_x"], 0.0)
        self.assertEqual(released["control_state"], "interrupted")
        self.assertGreater(released["vx"], 0.0)
        self.assertGreater(released["x"], before_release["x"])
        self.assertEqual(released["zone_id"], "hallway")

    def test_unsupported_body_profile_is_rejected(self):
        runtime = EmbodiedWorldRuntime()
        with self.assertRaises(ContractError) as caught:
            runtime.submit_spawn(
                request_id="spawn.bad-profile",
                entity_id="entity.yuki",
                embodiment_id="embodiment.yuki.primary",
                owner_id="character.yuki",
                kind="character",
                zone_id="hallway",
                spawn_id="yuki_day_start",
                controller_id="controller.yuki",
                body_profile_hash="f" * 64,
            )
        self.assertEqual(caught.exception.code, "unsupported_profile")

    def test_old_epoch_command_is_rejected_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "world.sqlite3"
            first = EmbodiedWorldRuntime(store=WorldCheckpointStore(path))
            spawn_yuki(first)
            old_epoch = first.epoch
            first.close()

            restored = EmbodiedWorldRuntime(store=WorldCheckpointStore(path))
            try:
                current = yuki(restored)
                queued = restored.submit_input(
                    request_id="input.old-epoch",
                    entity_id="entity.yuki",
                    expected_zone_id=current["zone_id"],
                    expected_world_epoch=old_epoch,
                    controller_id=current["controller_id"],
                    controller_generation=current["controller_generation"],
                    sequence=1,
                    motor_x=1.0,
                )
                restored.tick()
                receipt = restored.receipt(queued["action_id"])
                self.assertEqual(receipt["status"], "failed")
                self.assertEqual(receipt["reason_code"], "stale_world")
                self.assertEqual(yuki(restored)["motor_x"], 0.0)
            finally:
                restored.close()

    def test_training_setup_is_privileged_and_not_learned_success(self):
        runtime = EmbodiedWorldRuntime()
        spawn_yuki(runtime)
        with self.assertRaises(ContractError) as caught:
            runtime.submit_setup_reset(
                request_id="setup.denied", entity_id="entity.yuki",
                episode_id="episode.1", reason="prepare", privileged=False)
        self.assertEqual(caught.exception.code, "capability_denied")

        queued = runtime.submit_setup_reset(
            request_id="setup.allowed", entity_id="entity.yuki",
            episode_id="episode.1", reason="prepare flat course", privileged=True)
        runtime.tick()
        receipt = runtime.receipt(queued["action_id"])
        self.assertEqual(receipt["status"], "applied")
        self.assertFalse(receipt["observed_outcome"]["learned_success"])
        self.assertEqual(yuki(runtime)["zone_id"], "training/flat_run")

    def test_training_setup_can_place_episode_inside_training_lane(self):
        runtime = EmbodiedWorldRuntime()
        spawn_yuki(runtime)
        queued = runtime.submit_setup_reset(
            request_id="setup.x.731",
            entity_id="entity.yuki",
            episode_id="episode.x",
            reason="bounded Motor/Spine episode setup",
            x=731.0,
            privileged=True,
        )
        runtime.tick()
        receipt = runtime.receipt(queued["action_id"])
        self.assertEqual(receipt["status"], "applied")
        self.assertFalse(receipt["observed_outcome"]["learned_success"])
        self.assertEqual(yuki(runtime)["zone_id"], "training/flat_run")
        self.assertEqual(yuki(runtime)["x"], 731.0)

    def test_restart_restores_one_body_new_epoch_and_does_not_repeat_transfer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "world.sqlite3"
            runtime = EmbodiedWorldRuntime(
                controller_watchdog_ticks=5000,
                store=WorldCheckpointStore(path),
                checkpoint_interval_ticks=10000,
            )
            old_epoch = runtime.epoch
            drive_to_laboratory(runtime)
            before = yuki(runtime)
            self.assertEqual(len(runtime.latest_snapshot()["transfers"]), 1)
            runtime.close()

            restored = EmbodiedWorldRuntime(
                controller_watchdog_ticks=5000,
                store=WorldCheckpointStore(path),
                checkpoint_interval_ticks=10000,
            )
            try:
                self.assertNotEqual(restored.epoch, old_epoch)
                self.assertEqual(restored.previous_epoch, old_epoch)
                self.assertEqual(restored.world_tick, 0)
                entities = restored.latest_snapshot()["entities"]
                self.assertEqual(len([e for e in entities if e["entity_id"] == "entity.yuki"]), 1)
                current = yuki(restored)
                self.assertEqual(current["zone_id"], "laboratory")
                self.assertEqual(current["controller_generation"],
                                 before["controller_generation"] + 1)
                restored.tick()
                self.assertEqual(yuki(restored)["zone_id"], "laboratory")
                self.assertEqual(len(restored.latest_snapshot()["transfers"]), 1)
            finally:
                restored.close()

    def test_exact_duplicate_request_returns_existing_action_after_commit(self):
        runtime = EmbodiedWorldRuntime()
        first = runtime.submit_spawn(
            request_id="spawn.same",
            entity_id="entity.yuki", embodiment_id="embodiment.yuki.primary",
            owner_id="character.yuki", kind="character", zone_id="hallway",
            spawn_id="yuki_day_start", controller_id="controller.yuki",
        )
        runtime.tick()
        second = runtime.submit_spawn(
            request_id="spawn.same",
            entity_id="entity.yuki", embodiment_id="embodiment.yuki.primary",
            owner_id="character.yuki", kind="character", zone_id="hallway",
            spawn_id="yuki_day_start", controller_id="controller.yuki",
        )
        self.assertEqual(second["action_id"], first["action_id"])
        self.assertEqual(second["status"], "applied")
        with self.assertRaises(ContractError) as caught:
            runtime.submit_spawn(
                request_id="spawn.same",
                entity_id="entity.other", embodiment_id="embodiment.yuki.primary",
                owner_id="character.yuki", kind="character", zone_id="hallway",
                spawn_id="yuki_day_start", controller_id="controller.yuki",
            )
        self.assertEqual(caught.exception.code, "request_conflict")

    def test_incompatible_map_physics_hash_is_rejected(self):
        base = MapCatalog.load_default()
        manifests = []
        for map_id in base.map_ids():
            data = base.get(map_id).to_dict()
            if map_id == "hallway":
                data["physics"]["contract_sha256"] = "f" * 64
            manifests.append(MapManifest.from_dict(data))
        bad = MapCatalog(manifests)
        with self.assertRaises(ContractError) as caught:
            EmbodiedWorldRuntime(catalog=bad)
        self.assertEqual(caught.exception.code, "unsupported_profile")

    def test_direct_remote_transfer_endpoint_is_rejected(self):
        service = EmbodiedWorldService(port=0, physics_hz=240)
        try:
            with self.assertRaisesRegex(ProtocolError, "direct transfer is unsupported"):
                service.dispatch(message(
                    "transfer", entity_id="entity.yuki",
                    portal_id="portal.hallway.laboratory"))
        finally:
            service.shutdown()

    def test_scheduler_runs_independently_of_observers(self):
        service = EmbodiedWorldService(port=0, physics_hz=240)
        try:
            service.start()
            start = service.runtime.world_tick
            time.sleep(0.04)
            end = service.runtime.world_tick
            self.assertGreater(end, start)
            snapshot = service.runtime.latest_snapshot()
            snapshot["entities"].append({"forged": True})
            self.assertNotEqual(snapshot, service.runtime.latest_snapshot())
        finally:
            service.shutdown()


class EmbodiedGatewayTests(unittest.TestCase):
    def test_login_training_reset_and_logout_keep_one_persistent_body(self):
        world = EmbodiedWorldService(port=0, physics_hz=240)
        world.start()
        gateway = EmbodiedGatewayService(
            port=0, world_port=world.address[1]
        )
        try:
            login = gateway.dispatch(message("login", player_id="player1"))
            session_id = login["session_id"]
            self.assertEqual(login["entity_id"], "entity.yuki")
            self.assertEqual(login["world_id"], "yuki-world-v1")

            reset = gateway.dispatch(message(
                "training_reset", session_id=session_id, x=731.0
            ))
            self.assertEqual(reset["type"], "training_reset")
            state = gateway.dispatch(message(
                "snapshot", session_id=session_id
            ))
            self.assertEqual(state["observation"]["zone_id"], "training/flat_run")
            self.assertEqual(state["observation"]["physical"]["x"], 731.0)

            gateway.dispatch(message("logout", session_id=session_id))
            entities = world.runtime.latest_snapshot()["entities"]
            self.assertEqual(
                [item["entity_id"] for item in entities], ["entity.yuki"]
            )
            again = gateway.dispatch(message("login", player_id="player1"))
            self.assertEqual(again["zone_id"], "training/flat_run")
        finally:
            gateway.server.server_close()
            world.shutdown()


if __name__ == "__main__":
    unittest.main()
