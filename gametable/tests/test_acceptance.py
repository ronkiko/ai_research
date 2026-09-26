from __future__ import annotations

import ast
import json
from pathlib import Path
import tempfile
import time
import unittest

from gameserver.v1.world.embodied import (
    BODY_PROFILE_SHA256, EmbodiedWorldRuntime,
)
from gameserver.v1.world.embodied_server import EmbodiedWorldService
from gametable.roleplay.engine import load_rules
from gametable.roleplay.store import Store
from graphics.projector import SceneProjector
from graphics.stream import FrameHub


ROOT = Path(__file__).resolve().parents[2]


def entity(runtime: EmbodiedWorldRuntime, entity_id="entity.yuki"):
    return next(
        item for item in runtime.latest_snapshot()["entities"]
        if item["entity_id"] == entity_id
    )


def spawn_yuki(runtime: EmbodiedWorldRuntime):
    queued = runtime.submit_spawn(
        request_id="accept.spawn.yuki",
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
    receipt = runtime.receipt(queued["action_id"])
    if receipt["status"] != "applied":
        raise AssertionError(receipt)


def drive_until_zone(runtime, target_zone, direction, *, limit=1800):
    current = entity(runtime)
    queued = runtime.submit_input(
        request_id=f"accept.input.{current['zone_id']}.{target_zone}",
        entity_id="entity.yuki",
        expected_zone_id=current["zone_id"],
        expected_world_epoch=runtime.epoch,
        controller_id=current["controller_id"],
        controller_generation=current["controller_generation"],
        sequence=current["last_sequence"] + 1,
        motor_x=float(direction),
    )
    runtime.tick()
    if runtime.receipt(queued["action_id"])["status"] != "applied":
        raise AssertionError(runtime.receipt(queued["action_id"]))
    for _ in range(limit):
        runtime.tick()
        current = entity(runtime)
        if current["zone_id"] == target_zone:
            return current
    raise AssertionError(
        f"entity.yuki did not reach {target_zone}: {entity(runtime)}"
    )


class AcceptanceMatrixTests(unittest.TestCase):
    def test_a1_gametable_save_pins_the_migrated_embodiment_identity(self):
        rules = load_rules()
        binding = {
            "character_id": "character.yuki",
            "embodiment_id": "embodiment.yuki.primary",
            "entity_id": "entity.yuki",
            "player_id": "player1",
            "controller_id": "controller.yuki",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "save.sqlite3"
            store = Store(path, rules, identity_binding=binding)
            try:
                self.assertEqual(store.identity_binding(), binding)
            finally:
                store.close()

            reopened = Store(path, rules, identity_binding=binding)
            reopened.close()

            foreign = dict(binding)
            foreign["embodiment_id"] = "embodiment.other"
            with self.assertRaisesRegex(ValueError, "another embodiment identity"):
                Store(path, rules, identity_binding=foreign)

    def test_a1_a3_same_binding_crosses_all_zones_by_physical_portals_without_bounce(self):
        runtime = EmbodiedWorldRuntime(controller_watchdog_ticks=10000)
        spawn_yuki(runtime)
        original = entity(runtime)
        self.assertEqual(original["entity_id"], "entity.yuki")
        self.assertEqual(original["embodiment_id"], "embodiment.yuki.primary")

        laboratory = drive_until_zone(runtime, "laboratory", 1)
        self.assertEqual(laboratory["entity_id"], original["entity_id"])
        self.assertEqual(laboratory["embodiment_id"], original["embodiment_id"])
        self.assertEqual(laboratory["x"], 1.0)
        for _ in range(4):
            runtime.tick()
        self.assertEqual(entity(runtime)["zone_id"], "laboratory")

        training = drive_until_zone(runtime, "training/flat_run", 1)
        self.assertEqual(training["entity_id"], original["entity_id"])
        self.assertEqual(training["embodiment_id"], original["embodiment_id"])
        self.assertEqual(training["x"], 1.0)

        laboratory_back = drive_until_zone(runtime, "laboratory", -1)
        self.assertEqual(laboratory_back["x"], 899.0)
        for _ in range(4):
            runtime.tick()
        self.assertEqual(entity(runtime)["zone_id"], "laboratory")

        hallway = drive_until_zone(runtime, "hallway", -1)
        self.assertEqual(hallway["x"], 499.0)
        transfers = runtime.latest_snapshot()["transfers"]
        route = [
            item["receipt"]["target_zone"]
            for item in transfers
            if item["receipt"]["entity_id"] == "entity.yuki"
        ]
        self.assertEqual(
            route[-4:],
            ["laboratory", "training/flat_run", "laboratory", "hallway"],
        )
        self.assertTrue(all(
            item["receipt"]["status"] == "applied"
            for item in transfers[-4:]
        ))

    def test_a2_learned_navigation_has_no_scripted_or_actuator_fallback(self):
        navigation = (ROOT / "world/navigation.py").read_text(encoding="utf-8")
        escort = (
            ROOT / "organism/controllers/scripted_escort.py"
        ).read_text(encoding="utf-8")
        training = (
            ROOT / "organism/training.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("submit_input", navigation)
        self.assertNotIn("motor_x", navigation)
        self.assertNotIn("scripted_escort", navigation)
        self.assertIn("controller_mode", escort)
        self.assertNotIn("optimizer", escort.lower())
        self.assertNotIn("scripted_escort", training)

    def test_a4_world_clock_advances_without_llm_renderer_or_client(self):
        service = EmbodiedWorldService(port=0, physics_hz=240)
        try:
            service.start()
            before = service.runtime.world_tick
            time.sleep(0.04)
            after = service.runtime.world_tick
            self.assertGreater(after, before)
            self.assertGreaterEqual(after - before, 2)
        finally:
            service.shutdown()

    def test_a5_a6_learning_surface_is_bounded_and_active_config_has_only_new_mcps(self):
        source = (ROOT / "organism/mcp.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        tools = {}
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if any(
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and dec.func.attr == "tool"
                for dec in node.decorator_list
            ):
                tools[node.name] = [arg.arg for arg in node.args.args]
        expected = {
            "describe", "skills", "training_prepare",
            "motor_train_start", "spine_train_start",
            "training_status", "training_cancel",
            "verify_start", "verify_status", "verify_cancel", "skill_select",
        }
        self.assertEqual(set(tools), expected)
        forbidden = {
            "path", "file", "directory", "python", "code",
            "entity_id", "embodiment_id", "x", "vx", "motor_x",
        }
        self.assertTrue(all(not (forbidden & set(args)) for args in tools.values()))
        config = json.loads(
            (ROOT / "gametable/opencode.json").read_text(encoding="utf-8")
        )
        self.assertEqual(set(config["mcp"]), {"navigation_v1", "learning_v1"})
        opencode = (
            ROOT / "gametable/roleplay/opencode.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"action": "deny"', opencode)
        self.assertNotIn("gamelab_v1", opencode)
        self.assertNotIn("game_v1_", opencode)

    def test_a7_assisted_setup_is_not_success_and_verify_is_frozen_one_shot_contract(self):
        runtime = EmbodiedWorldRuntime()
        spawn_yuki(runtime)
        queued = runtime.submit_setup_reset(
            request_id="accept.setup",
            entity_id="entity.yuki",
            episode_id="accept.episode",
            reason="acceptance assisted setup",
            privileged=True,
        )
        runtime.tick()
        receipt = runtime.receipt(queued["action_id"])
        self.assertEqual(receipt["status"], "applied")
        self.assertFalse(receipt["observed_outcome"]["learned_success"])

        jobs = (ROOT / "organism/jobs.py").read_text(encoding="utf-8")
        registry = (
            ROOT / "organism/skill_registry.py"
        ).read_text(encoding="utf-8")
        school = (
            ROOT / "organism/motor_school.py"
        ).read_text(encoding="utf-8")
        self.assertIn("resume_interrupted: bool = False", jobs)
        self.assertIn("resume_interrupted=resume_interrupted", jobs)
        self.assertIn('verification.get("passed") is not True', registry)
        self.assertIn("certification_attempted", school)

    def test_a8_crash_window_becomes_uncertain_and_same_proposal_is_not_recreated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "save.sqlite3"
            rules = load_rules()
            store = Store(path, rules)
            proposal = {
                "proposal_id": "proposal.acceptance.crash",
                "source": "director_request",
                "action_type": "navigate",
                "target_id": "laboratory",
                "rationale": "acceptance",
                "observation_ref": {
                    "source": "world", "world_epoch": "epoch.1",
                    "observed_tick": 1, "location_id": "hallway",
                },
                "scope": {"capability": "navigate", "target_id": "laboratory"},
            }
            first = store.reserve_action("turn.acceptance", proposal)
            self.assertTrue(first["created"])
            store.close()

            recovered = Store(path, rules)
            try:
                action = recovered.action(proposal["proposal_id"])
                self.assertEqual(action["status"], "uncertain")
                same = recovered.reserve_action("turn.acceptance", proposal)
                self.assertFalse(same["created"])
                self.assertEqual(same["status"], "uncertain")
            finally:
                recovered.close()

    def test_a9_render_projection_has_1001_cells_two_layers_and_stale_fence(self):
        projector = SceneProjector()
        snapshot = {
            "world_id": "yuki-world-v1",
            "world_epoch": "epoch.accept",
            "world_tick": 10,
            "world_revision": 10,
            "entities": [
                {
                    "entity_id": "entity.yuki", "owner_id": "character.yuki",
                    "zone_id": "hallway", "x": 10.2, "vx": 0.0,
                },
                {
                    "entity_id": "entity.director",
                    "owner_id": "character.director",
                    "zone_id": "hallway", "x": 10.8, "vx": 0.0,
                },
            ],
        }
        frame = projector.project(snapshot, focus_entity_id="entity.yuki")
        terrain = projector.terrain("hallway")
        layers = projector.layers(frame, terrain)
        self.assertEqual(len(layers["terrain"]), 1001)
        self.assertEqual(len(layers["occupancy"]), 1001)
        self.assertEqual(
            layers["occupancy"][10],
            ["entity.yuki", "entity.director"],
        )
        hub = FrameHub()
        first = hub.publish(frame)
        stale = dict(frame)
        stale["frame_id"] = "frame:epoch.accept:9:hallway"
        stale["source_world_revision"] = 9
        stale["source_world_tick"] = 9
        self.assertIsNone(hub.publish(stale))
        self.assertEqual(hub.since(0), [first])

    def test_a10_cutover_has_no_active_gamelab_dependency(self):
        config = json.loads(
            (ROOT / "gametable/opencode.json").read_text(encoding="utf-8")
        )
        self.assertEqual(set(config["mcp"]), {"navigation_v1", "learning_v1"})
        start = (ROOT / "gametable/op/start.sh").read_text(encoding="utf-8")
        self.assertNotIn("gamelab", start.lower())
        self.assertIn("gameserver/v1/op/embodied.sh", start)
        self.assertIn("organism/op/organism.sh", start)
        self.assertIn("gametable.migration", start)

    def test_a11_fresh_story_and_manual_input_release_are_explicit(self):
        start = (ROOT / "gametable/op/start.sh").read_text(encoding="utf-8")
        shell = (ROOT / "gametable/web/js/shell.js").read_text(encoding="utf-8")
        gui = (
            ROOT / "gameclient/v1/clients/gui.py"
        ).read_text(encoding="utf-8")
        self.assertIn("WORLD_STATE", start)
        self.assertIn("stop_stack", start)
        self.assertIn("тело начнёт день у EXIT", start)
        self.assertIn("focusin", shell)
        self.assertIn("releaseDirector()", shell)
        self.assertIn("retryableEscort", shell)
        self.assertIn("Продолжить сопровождение", shell)
        self.assertIn('"entity.yuki"', gui)
        self.assertIn('"entity.director"', gui)
        self.assertIn('return "P"', gui)
        self.assertIn('return "D"', gui)


    def test_a12_browser_is_separated_by_player_gateway(self):
        gateway = (ROOT / "player-gateway/src/server.mjs").read_text(encoding="utf-8")
        proxy = (ROOT / "player-gateway/src/proxy.mjs").read_text(encoding="utf-8")
        shell = (ROOT / "gametable/web/js/shell.js").read_text(encoding="utf-8")
        frames = (ROOT / "gametable/web/js/frames.js").read_text(encoding="utf-8")
        api = (ROOT / "gametable/web/js/api.js").read_text(encoding="utf-8")
        backend = (ROOT / "gametable/roleplay/server.py").read_text(encoding="utf-8")
        start = (ROOT / "gametable/op/start.sh").read_text(encoding="utf-8")

        self.assertIn("player-gateway/op/gateway.sh", start)
        self.assertIn("window.io", frames)
        self.assertIn("gatewayAcquire", shell)
        self.assertIn("gatewayInput", shell)
        self.assertNotIn("/api/director/", api)
        self.assertIn('pathname === "/api/frames"', proxy)
        self.assertIn('pathname.startsWith("/api/director/")', proxy)
        self.assertNotIn("17600", gateway)
        self.assertNotIn("GATEWAY_PORT", gateway)
        self.assertNotIn('path == "/api/frames"', backend)
        self.assertNotIn("EmbodiedWorldGraphics", backend)

    def test_a13_web_frontend_is_bounded_and_failure_isolated(self):
        server = (ROOT / "player-gateway/src/server.mjs").read_text(encoding="utf-8")
        config = (ROOT / "player-gateway/src/config.mjs").read_text(encoding="utf-8")
        core = (ROOT / "player-gateway/src/core.mjs").read_text(encoding="utf-8")
        session = (ROOT / "player-gateway/src/session.mjs").read_text(encoding="utf-8")
        start = (ROOT / "gametable/op/start.sh").read_text(encoding="utf-8")

        self.assertIn('io.volatile.emit("frame.latest"', server)
        self.assertIn("maxHttpBufferSize", server)
        self.assertIn("maxConnectionsPerIp", server)
        self.assertIn("PLAYER_GATEWAY_PUBLIC=1", config)
        self.assertIn("TLS termination contract", config)
        self.assertIn("PLAYER_GATEWAY_SESSION_SECRET", config)
        self.assertIn("HttpOnly", session)
        self.assertIn("SameSite=Strict", session)
        self.assertIn("await this.release(this.owner)", core)
        self.assertIn("frames_published", core)
        self.assertIn("coalesced", (ROOT / "player-gateway/src/input-buffer.mjs").read_text(encoding="utf-8"))
        self.assertIn('"$ROOT/player-gateway/op/gateway.sh" --stop', start)


    def test_a14_world_state_reads_are_shared_and_atomic(self):
        world = (ROOT / "gameserver/v1/world/embodied.py").read_text(encoding="utf-8")
        world_server = (
            ROOT / "gameserver/v1/world/embodied_server.py"
        ).read_text(encoding="utf-8")
        gateway = (
            ROOT / "gameserver/v1/gateway/embodied.py"
        ).read_text(encoding="utf-8")
        hub = (
            ROOT / "gameserver/v1/gateway/state_hub.py"
        ).read_text(encoding="utf-8")
        escort = (
            ROOT / "organism/controllers/scripted_escort.py"
        ).read_text(encoding="utf-8")
        host = (
            ROOT / "gameclient/v1/host/server.py"
        ).read_text(encoding="utf-8")

        self.assertIn("def state_frame(self)", world)
        self.assertIn('kind == "state_frame"', world_server)
        self.assertIn("WorldStateHub", gateway)
        self.assertNotIn('self._world("snapshot")', gateway)
        self.assertIn('self.connection.request("state_frame")', hub)
        self.assertIn("WORLD_STATE_HUB_HZ", hub)
        self.assertIn("upstream_age_seconds", host)
        self.assertIn("HostClient", escort)
        self.assertNotIn("EMBODIED_WORLD_PORT", escort)
        self.assertNotIn('message("snapshot"', escort)


if __name__ == "__main__":
    unittest.main()
