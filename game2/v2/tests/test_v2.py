from __future__ import annotations

import ast
import inspect
import io
import json
import socket
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest import mock

from game2.v2.console.config import (ControllerManifest, DisplayManifest,
                                      EngineManifest, InternalManifest, SessionConfig,
                                      allocate_endpoint)
from game2.v2.console.controller.controller import ControllerService
from game2.v2.console.display.display import DisplayService
from game2.v2.console.display.view_state import DisplayState
from game2.v2.console.engine.engine import Engine, EngineService
from game2.v2.console.engine.physics import PhysicsConfig
from game2.v2.console.main import run_session
from game2.v2.console.protocol import (ActionCommand, action_message,
                                        decode_control_message)
from game2.v2.console.transport.control_server import ControlEnvelope, ControlServer
from game2.v2.console.world import load_world
from game2.v2.console.transport.publisher import EventPublisher, LatestPublisher
from game2.v2.contracts.framing import (ProtocolError, decode_frame, encode_frame,
                                        recv_frame)
from game2.v2.contracts.joystick import (JoystickState, decode_joystick_message,
                                          joystick_ack, joystick_message)
from game2.v2.contracts.manifests import Endpoint, PeripheralManifest
from game2.v2.tests.architecture_helpers import absolute_imports, imports_from_tree
from game2.v2.tests.harness import run_realtime_smoke

ROOT = Path(__file__).resolve().parents[3]
V2 = ROOT / "game2" / "v2"
PIT = V2 / "console" / "world" / "maps" / "pit.json"


class StructureTests(unittest.TestCase):
    def test_required_domains_have_readmes_and_docs(self):
        for name in ("console", "player", "training", "management", "contracts"):
            domain = V2 / name
            self.assertTrue(domain.is_dir(), name)
            self.assertTrue((domain / "__init__.py").is_file(), name)
            self.assertTrue((domain / "README.md").is_file(), name)
            self.assertTrue((domain / "doc").is_dir(), name)

    def test_required_subsystems_have_readmes_and_docs(self):
        for relative in (
            "console/engine", "console/controller", "console/display", "console/transport",
            "console/display/screen", "console/display/vision",
            "player/scripted", "player/human", "tests",
        ):
            directory = V2 / relative
            self.assertTrue((directory / "README.md").is_file(), relative)
            self.assertTrue((directory / "doc").is_dir(), relative)

    def test_relocated_entrypoints_and_flat_modules(self):
        for relative in (
            "console/main.py", "console/engine/main.py", "console/controller/main.py",
            "console/display/main.py", "player/scripted/main.py", "player/human/main.py",
            "management/main.py",
        ):
            self.assertTrue((V2 / relative).is_file(), relative)
        for relative in (
            "console.py", "config.py", "controller.py", "display.py", "engine.py",
            "joystick.py", "protocol.py", "ui.py", "players/scripted.py",
        ):
            self.assertFalse((V2 / relative).exists(), relative)

    def test_domain_import_boundaries(self):
        forbidden = {
            "console": ("game2.v2.player", "game2.v2.training", "game2.v2.management"),
            "player": ("game2.v2.console", "game2.v2.training", "game2.v2.management"),
            "training": ("game2.v2.console", "game2.v2.management"),
            "management": ("game2.v2.console", "game2.v2.player", "game2.v2.training"),
            "contracts": ("game2.v2.console", "game2.v2.player", "game2.v2.training",
                          "game2.v2.management"),
        }
        for domain, denied in forbidden.items():
            for source in (V2 / domain).rglob("*.py"):
                imported = absolute_imports(source, V2)
                leaked = [module for module in imported
                          if any(module == prefix or module.startswith(prefix + ".")
                                 for prefix in denied)]
                self.assertEqual(leaked, [], f"{source}: {leaked}")

    def test_relative_imports_are_normalized_before_boundary_rules(self):
        player_tree = ast.parse(
            "from ..console import something\n"
            "from ..contracts import JoystickState\n"
            "from .sibling import helper\n"
        )
        imported = imports_from_tree(player_tree, "game2.v2.player.foo")
        self.assertIn("game2.v2.console", imported)
        self.assertIn("game2.v2.contracts", imported)
        self.assertIn("game2.v2.player.sibling", imported)

        denied = ("game2.v2.console", "game2.v2.training", "game2.v2.management")
        leaked = [module for module in imported
                  if any(module == prefix or module.startswith(prefix + ".")
                         for prefix in denied)]
        self.assertEqual(leaked, ["game2.v2.console"])

    def test_management_rejects_absolute_and_relative_domain_imports(self):
        cases = (
            "from game2.v2.console.main import run_session\n",
            "from ..console.main import run_session\n",
        )
        denied = ("game2.v2.console", "game2.v2.player", "game2.v2.training")
        for source in cases:
            imported = imports_from_tree(ast.parse(source), "game2.v2.management.foo")
            self.assertIn("game2.v2.console.main", imported)
            leaked = [module for module in imported
                      if any(module == prefix or module.startswith(prefix + ".")
                             for prefix in denied)]
            self.assertIn("game2.v2.console.main", leaked)
            self.assertTrue(all(module.startswith("game2.v2.console.main")
                                for module in leaked))

    def test_scripted_player_imports_only_public_v2_modules(self):
        imported = absolute_imports(V2 / "player" / "scripted" / "main.py", V2)
        v2_imports = {module for module in imported if module.startswith("game2.v2.")}
        self.assertTrue(v2_imports)
        self.assertTrue(all(module.startswith("game2.v2.contracts") for module in v2_imports))

    def test_human_player_imports_only_public_contracts_and_local_modules(self):
        for source in (V2 / "player" / "human").rglob("*.py"):
            imported = absolute_imports(source, V2)
            v2_imports = {module for module in imported if module.startswith("game2.v2.")}
            self.assertTrue(all(
                module.startswith("game2.v2.contracts")
                or module.startswith("game2.v2.player.human")
                for module in v2_imports
            ), str(source))
            text = source.read_text(encoding="utf-8")
            for forbidden in ("ActionCommand", "target_world_tick", "hold_ticks", "InternalManifest",
                              "ControllerManifest", "DisplayManifest"):
                self.assertNotIn(forbidden, text, str(source))


class ManifestAndWorldTests(unittest.TestCase):
    def test_internal_and_peripheral_manifests_are_separate(self):
        internal = InternalManifest("session", Endpoint("127.0.0.1", 12345),
                                   Endpoint("127.0.0.1", 12346), None, None, "/tmp/run")
        peripheral = PeripheralManifest("session", Endpoint("127.0.0.1", 12347))
        self.assertEqual(InternalManifest.from_dict(internal.to_dict()), internal)
        self.assertEqual(PeripheralManifest.from_dict(peripheral.to_dict()), peripheral)
        self.assertEqual(set(peripheral.to_dict()), {"session_id", "joystick", "vision"})
        self.assertNotIn("engine_control", peripheral.to_dict())
        self.assertNotIn("engine_state", peripheral.to_dict())
        self.assertNotIn("engine_telemetry", peripheral.to_dict())
        self.assertNotIn("engine_events", peripheral.to_dict())

    def test_manifest_rejects_engine_leakage(self):
        with self.assertRaises(ValueError):
            PeripheralManifest.from_dict({
                "session_id": "s", "joystick": {"host": "127.0.0.1", "port": 1},
                 "vision": None,
                 "engine_control": {"host": "127.0.0.1", "port": 2},
            })
        with self.assertRaises(ValueError):
            PeripheralManifest.from_dict({
                "session_id": "s", "joystick": {"host": "127.0.0.1", "port": 1},
            })

    def test_subsystem_manifests_have_only_required_capabilities(self):
        control = Endpoint("127.0.0.1", 12345)
        telemetry = Endpoint("127.0.0.1", 12346)
        state = Endpoint("127.0.0.1", 12347)
        joystick = Endpoint("127.0.0.1", 12348)
        controller = ControllerManifest("s", control, telemetry, joystick)
        display = DisplayManifest("s", state, str(PIT), "vision")
        engine = EngineManifest("s", control, state, telemetry, None, "/tmp/run")
        self.assertEqual(set(controller.to_dict()),
                         {"session_id", "engine_control", "engine_telemetry", "joystick"})
        self.assertEqual(set(display.to_dict()),
                         {"session_id", "engine_state", "world_file", "mode", "vision"})
        self.assertEqual(set(engine.to_dict()),
                         {"session_id", "control", "state", "telemetry", "events", "run_dir"})
        self.assertNotIn("engine_state", controller.to_dict())
        self.assertNotIn("engine_control", display.to_dict())

    def test_config_has_distinct_realtime_and_unpaced_smokes(self):
        realtime = SessionConfig.from_file(V2 / "console" / "configs" / "realtime-smoke.json")
        unpaced = SessionConfig.from_file(V2 / "console" / "configs" / "unpaced-smoke.json")
        self.assertEqual(realtime.clock_mode, "realtime")
        self.assertEqual(unpaced.clock_mode, "unpaced")
        self.assertEqual(realtime.controller, "default")
        self.assertEqual(unpaced.controller, "default")
        self.assertEqual(realtime.display_mode, "vision")
        self.assertEqual(unpaced.display_mode, "vision")
        self.assertEqual(SessionConfig.from_dict({"map": str(PIT)}).display_mode, "vision")
        with self.assertRaises(ValueError):
            SessionConfig.from_dict({"map": str(PIT), "display_mode": "debug"})

    def test_console_rejects_management_ui_configuration(self):
        with self.assertRaises(ValueError):
            SessionConfig.from_dict({"map": str(PIT), "enable_ui": True})
        self.assertFalse(hasattr(SessionConfig, "enable_ui"))
        for relative in (
            "console/config.py", "console/main.py",
            "console/configs/realtime-smoke.json", "console/configs/unpaced-smoke.json",
        ):
            self.assertNotIn("enable_ui", (V2 / relative).read_text(encoding="utf-8"))

    def test_root_readme_describes_the_research_and_realtime_contract(self):
        readme = (V2 / "README.md").read_text(encoding="utf-8")
        for concept in ("real-time", "research", "Player", "latency"):
            self.assertIn(concept, readme)
        for reference in ("doc/REALTIME_SYSTEM.md", "ARCHITECTURE.md", "console/SPEC.md",
                          "console/doc/MMO_SERVER_MODEL.md"):
            self.assertIn(reference, readme)

    def test_mmo_server_model_is_normative_inventory(self):
        document = (V2 / "console" / "doc" / "MMO_SERVER_MODEL.md").read_text(encoding="utf-8")
        for term in ("authoritative", "world_tick", "zero or many Players",
                     "Player / Connection / Actor", "Respawn is actor-local",
                     "Engine does not own Training episodes"):
            self.assertIn(term, document)

    def test_world_loader_and_fixed_world(self):
        loaded = load_world(PIT)
        self.assertEqual((loaded.width, loaded.height), (1280, 768))
        self.assertEqual(PhysicsConfig(hz=120).dt, 1 / 120)
        with self.assertRaises(ValueError):
            PhysicsConfig(hz=0)
        engine = Engine(loaded)
        self.assertIs(engine.avatar, engine.physics.body)
        engine.tick()
        self.assertEqual(engine.world_tick, 1)
        self.assertFalse(hasattr(engine, "session_tick"))
        self.assertFalse(hasattr(engine, "episode_tick"))
        self.assertIsNot(engine.world_state().avatar, engine.avatar)

    def test_physical_state_is_immutable(self):
        state = Engine(load_world(PIT)).world_state()
        with self.assertRaises(FrozenInstanceError):
            state.avatar.x = 9


class JoystickContractTests(unittest.TestCase):
    def test_all_four_button_states_are_valid(self):
        for sequence, right, jump in ((1, False, False), (2, True, False),
                                      (3, False, True), (4, True, True)):
            state = decode_joystick_message(joystick_message(JoystickState(sequence, right, jump)))
            self.assertEqual((state.right, state.jump), (right, jump))

    def test_strict_fields_and_types(self):
        valid = joystick_message(JoystickState(1, True, False))
        for invalid in (
            {**valid, "unknown": False},
            {**valid, "right": 1},
            {**valid, "type": "action"},
        ):
            with self.assertRaises(ProtocolError):
                decode_joystick_message(invalid)

    def test_sequence_and_ack(self):
        with self.assertRaises(ProtocolError):
            JoystickState(0, False, False)
        ack = joystick_ack(42, "accepted")
        self.assertEqual((ack["sequence"], ack["status"]), (42, "accepted"))
        self.assertEqual(joystick_ack(42, "duplicate")["status"], "duplicate")
        self.assertEqual(joystick_ack(42, "rejected")["status"], "rejected")

    def test_player_is_not_an_engine_command_client(self):
        source = (V2 / "player" / "scripted" / "main.py").read_text(encoding="utf-8")
        self.assertNotIn("InternalManifest", source)
        self.assertNotIn("ActionCommand", source)
        self.assertNotIn("engine_control", source)
        self.assertNotIn("game2.v2.console", source)


class InternalSchedulingTests(unittest.TestCase):
    def test_action_timing_remains_private_to_engine_boundary(self):
        engine = Engine(load_world(PIT))
        self.assertEqual(engine.submit_action(ActionCommand(1, 3, 2, True)), "accepted")
        engine.tick()
        engine.tick()
        self.assertEqual(engine.avatar.vx, 0)
        engine.tick()
        self.assertGreater(engine.avatar.vx, 0)
        speed = engine.avatar.vx
        engine.tick()
        engine.tick()
        engine.tick()
        self.assertLess(engine.avatar.vx, speed)

    def test_duplicate_and_late_actions_are_rejected(self):
        engine = Engine(load_world(PIT))
        command = ActionCommand(1, 3, 1)
        self.assertEqual(engine.submit_action(command), "accepted")
        self.assertEqual(engine.submit_action(command), "duplicate")
        engine.tick()
        engine.tick()
        engine.tick()
        self.assertEqual(engine.submit_action(ActionCommand(2, 1, 1)), "late")

    def test_action_validation_uses_global_world_time_not_episode(self):
        engine = Engine(load_world(PIT))
        engine.tick()
        engine.reset()
        self.assertEqual(engine.world_tick, 1)
        self.assertEqual(engine.submit_action(ActionCommand(1, 3, 1)), "accepted")

    def test_action_command_has_only_global_target_time(self):
        command = ActionCommand(sequence=1, target_world_tick=7, hold_ticks=2,
                                right=True, jump=False)
        message = action_message(command)
        self.assertEqual(set(message), {
            "version", "type", "sequence", "target_world_tick", "hold_ticks",
            "right", "jump",
        })
        self.assertEqual(decode_control_message(message), command)
        with self.assertRaises(ProtocolError):
            decode_control_message({**message, "episode": 1})
        with self.assertRaises(ProtocolError):
            decode_control_message({**message, "target_tick": 7})

    def test_state_and_telemetry_publish_one_canonical_world_timestamp(self):
        engine = Engine(load_world(PIT))
        for payload in (engine.world_state().to_payload(), engine.telemetry().to_payload()):
            self.assertIn("world_tick", payload)
            for legacy in ("tick", "session_tick", "episode_tick"):
                self.assertNotIn(legacy, payload)

    def test_compatibility_reset_restarts_elapsed_origin_not_global_time(self):
        engine = Engine(load_world(PIT))
        engine.tick()
        engine.tick()
        old_world_tick = engine.world_tick
        reset_events = engine.reset()
        self.assertEqual(engine.world_tick, old_world_tick)
        self.assertEqual(engine.episode_elapsed, 0)
        self.assertEqual(reset_events[0]["world_tick"], old_world_tick)
        engine.tick()
        self.assertGreater(engine.world_tick, old_world_tick)
        self.assertEqual(engine.episode_elapsed, 1)

    def test_engine_has_one_runtime_clock_and_physics_has_no_second_clock(self):
        engine_source = (V2 / "console" / "engine" / "engine.py").read_text(encoding="utf-8")
        physics_source = (V2 / "console" / "engine" / "physics.py").read_text(encoding="utf-8")
        self.assertNotIn("session_tick", engine_source)
        self.assertNotIn("episode_tick", engine_source)
        self.assertNotIn("self.tick", physics_source)
        engine = Engine(load_world(PIT))
        for expected in range(1, 4):
            engine.tick()
            self.assertEqual(engine.world_tick, expected)

    def test_engine_service_runs_without_player(self):
        with tempfile.TemporaryDirectory() as run_dir:
            config = SessionConfig(map=str(PIT), clock_mode="unpaced", enable_state=False,
                                   enable_telemetry=False, enable_events=False, world_ticks=8)
            manifest = InternalManifest("no-player", allocate_endpoint(), None, None, None, run_dir)
            service = EngineService(
                Engine(load_world(PIT), session_id="no-player"),
                EngineManifest("no-player", manifest.engine_control, None, None, None, run_dir),
                config,
            )
            summary = service.run()
            self.assertEqual(summary["world_ticks"], 8)


class TerminalStateTests(unittest.TestCase):
    def _schedule_future_action(self, engine):
        self.assertEqual(engine.submit_action(ActionCommand(1, 10, 5, True)),
                         "accepted")
        self.assertEqual(len(engine._scheduled), 5)

    @staticmethod
    def _avatar_state(engine):
        avatar = engine.avatar
        return (avatar.x, avatar.y, avatar.vx, avatar.vy,
                avatar.grounded, avatar.alive)

    def test_dead_terminal_freezes_physics_and_rejects_future_actions(self):
        world = load_world(PIT)
        engine = Engine(world)
        engine.avatar.x, engine.avatar.y = 512, 500
        engine.avatar.vx, engine.avatar.vy = 0, 30_000
        engine.avatar.grounded = False
        self._schedule_future_action(engine)

        events = engine.tick()

        self.assertIn({"event": "death", "reason": "damage_surface", "world_tick": 1}, events)
        self.assertEqual(engine.terminal, "dead")
        self.assertEqual(len(engine._scheduled), 0)
        world_state = engine.world_state()
        self.assertEqual(world_state.terminal, "dead")
        self.assertEqual(world_state.to_payload()["terminal"], "dead")
        display_state = DisplayState.from_payload(
            world_state.to_payload(), "local", world)
        self.assertEqual(display_state.terminal, "dead")
        avatar = self._avatar_state(engine)
        world_tick = engine.world_tick
        with mock.patch.object(engine.physics, "step", wraps=engine.physics.step) as step:
            for sequence in range(2, 52):
                self.assertEqual(
                    engine.submit_action(ActionCommand(sequence, 100, 1)),
                    "rejected",
                )
                engine.tick()
            step.assert_not_called()
        self.assertEqual(self._avatar_state(engine), avatar)
        self.assertEqual(engine.terminal, "dead")
        self.assertEqual(engine.world_tick, world_tick + 50)
        self.assertEqual(len(engine._scheduled), 0)
        self.assertEqual(engine.stats.rejected, 50)

    def test_success_terminal_freezes_physics_and_reaches_display(self):
        world = load_world(PIT)
        engine = Engine(world)
        engine.avatar.x, engine.avatar.y = world.goal.x, world.goal.y
        engine.avatar.vx = engine.avatar.vy = 0
        engine.avatar.grounded = True
        self._schedule_future_action(engine)

        events = engine.tick()

        self.assertIn({"event": "goal_reached", "world_tick": 1}, events)
        self.assertEqual(engine.terminal, "success")
        self.assertEqual(len(engine._scheduled), 0)
        world_state = engine.world_state()
        self.assertEqual(world_state.terminal, "success")
        self.assertEqual(world_state.to_payload()["terminal"], "success")
        display_state = DisplayState.from_payload(
            world_state.to_payload(), "local", world)
        self.assertEqual(display_state.terminal, "success")
        avatar = self._avatar_state(engine)
        world_tick = engine.world_tick
        with mock.patch.object(engine.physics, "step", wraps=engine.physics.step) as step:
            for sequence in range(2, 52):
                self.assertEqual(
                    engine.submit_action(ActionCommand(sequence, 100, 1)),
                    "rejected",
                )
                engine.tick()
            step.assert_not_called()
        self.assertEqual(self._avatar_state(engine), avatar)
        self.assertEqual(engine.terminal, "success")
        self.assertEqual(engine.world_tick, world_tick + 50)
        self.assertEqual(len(engine._scheduled), 0)
        self.assertEqual(engine.stats.rejected, 50)

    def test_timeout_terminal_clears_actions_and_rejects_commands(self):
        engine = Engine(load_world(PIT), episode_limit=1)
        self._schedule_future_action(engine)

        engine.tick()

        self.assertEqual(engine.terminal, "timeout")
        self.assertEqual(len(engine._scheduled), 0)
        for sequence in range(2, 102):
            self.assertEqual(
                engine.submit_action(ActionCommand(sequence, 100, 1)),
                "rejected",
            )
        self.assertEqual(engine.stats.rejected, 100)
        self.assertEqual(len(engine._scheduled), 0)

class ChannelTests(unittest.TestCase):
    def test_partial_tcp_reads_and_malformed_packet(self):
        left, right = socket.socketpair()
        try:
            payload = encode_frame({"version": 1, "type": "state", "world_tick": 4})
            left.sendall(payload[:2])
            left.sendall(payload[2:])
            self.assertEqual(recv_frame(right)["world_tick"], 4)
            left.sendall(b"\x00\x00\x00\x03bad")
            with self.assertRaises(ProtocolError):
                recv_frame(right)
        finally:
            left.close()
            right.close()

    def test_joystick_server_uses_joystick_decoder(self):
        from game2.v2.contracts.joystick import decode_joystick_message
        server = ControlServer("127.0.0.1", 0, decoder=decode_joystick_message)
        server.start()
        client = socket.create_connection((server.host, server.port))
        client.settimeout(1)
        try:
            self.assertTrue(server.connected_event.wait(1))
            client.sendall(encode_frame(joystick_message(JoystickState(1, True, True))))
            deadline = time.monotonic() + 1
            while server.commands.empty() and time.monotonic() < deadline:
                time.sleep(0.001)
            envelope = server.drain()[0]
            self.assertIsInstance(envelope, ControlEnvelope)
            self.assertIsInstance(envelope.command, JoystickState)
        finally:
            client.close()
            server.close()

    def test_publishers_do_not_block_engine(self):
        publisher = LatestPublisher("127.0.0.1", 0)
        publisher.start()
        client = socket.create_connection((publisher.host, publisher.port))
        try:
            for world_tick in range(1000):
                publisher.publish({"version": 1, "type": "state", "world_tick": world_tick})
            self.assertLess(publisher.subscriber_count(), 2)
        finally:
            client.close()
            publisher.close()

        events = EventPublisher("127.0.0.1", 0, max_events=2)
        events.start()
        events.close()


class BoundaryTests(unittest.TestCase):
    def test_static_import_boundaries(self):
        player = inspect.getsource(__import__("game2.v2.player.scripted.main", fromlist=["main"]))
        controller = inspect.getsource(__import__("game2.v2.console.controller.controller", fromlist=["main"]))
        display = inspect.getsource(__import__("game2.v2.console.display.display", fromlist=["main"]))
        engine = (V2 / "console" / "engine" / "engine.py").read_text(encoding="utf-8")
        console = (V2 / "console" / "main.py").read_text(encoding="utf-8")
        self.assertNotIn("InternalManifest", player)
        self.assertNotRegex(player, r"(?:import|from).*engine|ActionCommand")
        self.assertNotIn("target_world_tick", player)
        self.assertNotIn("hold_ticks", player)
        self.assertNotRegex(controller, r"(?:import|from).*display|(?:import|from).*ui")
        self.assertNotRegex(display, r"(?:import|from).*(?:controller|player|model|ui)")
        self.assertNotRegex(engine, r"(?:import|from)\s+(?:pygame|torch|controller|display|ui|model|trainer|training)")
        self.assertNotIn("joystick.send", console)
        self.assertNotIn("recv_frame", console)
        self.assertNotIn("torch", console)
        self.assertNotIn("pygame", console)
        self.assertNotIn("players.scripted", console)
        self.assertNotIn("ScriptedPlayer", console)
        self.assertNotIn("launch_player", console)
        self.assertNotIn("player.log", console)
        self.assertNotIn("engine", (V2 / "management" / "main.py").read_text(encoding="utf-8").lower())
        joystick_source = (V2 / "contracts" / "joystick.py").read_text(encoding="utf-8")
        self.assertNotIn("torch", joystick_source)
        self.assertNotIn("physics", joystick_source)
        self.assertNotIn("model", joystick_source)

    def test_controller_and_display_capabilities_are_narrow(self):
        controller = ControllerManifest("s", Endpoint("127.0.0.1", 1),
                                       Endpoint("127.0.0.1", 2), Endpoint("127.0.0.1", 3))
        display = DisplayManifest("s", Endpoint("127.0.0.1", 4), str(PIT), "vision")
        self.assertEqual(set(controller.to_dict()),
                         {"session_id", "engine_control", "engine_telemetry", "joystick"})
        self.assertEqual(set(display.to_dict()),
                         {"session_id", "engine_state", "world_file", "mode", "vision"})
        self.assertNotIn("engine_state", controller.to_dict())
        self.assertNotIn("engine_control", display.to_dict())
        self.assertNotIn("engine_telemetry", display.to_dict())
        self.assertNotIn("joystick", display.to_dict())

    def test_console_uses_separate_processes(self):
        source = (V2 / "console" / "main.py").read_text(encoding="utf-8")
        self.assertIn('"game2.v2.console.controller.main"', source)
        self.assertIn('"game2.v2.console.display.main"', source)
        self.assertNotIn("router", source)
        self.assertNotIn("trainer", source.lower())
        self.assertNotIn("model", source.lower())

    def test_realtime_player_vertical_and_logs(self):
        status, player_status, ready = run_realtime_smoke(
            V2 / "console" / "configs" / "realtime-smoke.json")
        self.assertEqual((status, player_status), (0, 0))
        run_dir = V2 / "console" / "runs" / ready["session_id"]
        self.assertTrue((run_dir / "console.log").exists())
        self.assertTrue((run_dir / "controller.log").exists())
        self.assertTrue((run_dir / "display.log").exists())
        self.assertFalse((run_dir / "player.log").exists())
        display_log = (run_dir / "display.log").read_text(encoding="utf-8")
        self.assertNotRegex(display_log, r"state|avatar|\bx\b|\by\b|\bvx\b|\bvy\b|grounded")

    def test_unpaced_smoke_and_ui_trainer_absence(self):
        status, summary = run_session(V2 / "console" / "configs" / "unpaced-smoke.json")
        self.assertEqual(status, 0)
        self.assertEqual(summary["clock"], "unpaced")
        self.assertFalse(summary["display"])

    def test_display_does_not_change_authoritative_result(self):
        with tempfile.TemporaryDirectory() as directory:
            base = {**json.loads((V2 / "console" / "configs" / "realtime-smoke.json").read_text()),
                    "map": str(PIT)}
            paths = []
            for mode in ("disabled", "vision"):
                enabled = mode != "disabled"
                config = {**base, "enable_display": enabled,
                          "display_mode": "vision"}
                path = Path(directory) / f"display-{mode}.json"
                path.write_text(json.dumps(config), encoding="utf-8")
                paths.append(path)
            off_status, off = run_session(paths[0])
            on_status, on = run_session(paths[1])
        self.assertEqual((off_status, on_status), (0, 0))
        self.assertEqual(off["avatar"], on["avatar"])

    def test_display_has_no_public_video_payload(self):
        service = DisplayService(DisplayManifest("s", Endpoint("127.0.0.1", 12345),
                                                 str(PIT), "vision"))
        self.assertFalse(hasattr(service, "output"))
        self.assertFalse(hasattr(service, "publish"))
        self.assertFalse(hasattr(service, "state"))
        source = (V2 / "console" / "display" / "display.py").read_text(encoding="utf-8")
        self.assertNotIn("video_frame", source)
        self.assertNotIn('"state": snapshot', source)
        self.assertNotIn("ControlServer", source)

    def test_controller_ack_waits_for_engine_and_maps_late(self):
        manifest = ControllerManifest("s", Endpoint("127.0.0.1", 12345),
                                     Endpoint("127.0.0.1", 12346), Endpoint("127.0.0.1", 12347))
        service = ControllerService(manifest)
        responses = []

        class FakeJoystick:
            def respond(self, client_id, payload):
                responses.append((client_id, payload))

        class FakeEngine:
            def __init__(self):
                self.sent = []

            def sendall(self, payload):
                self.sent.append(payload)

        service.joystick = FakeJoystick()
        service.engine_control = FakeEngine()
        service.latest = {"episode": 1, "world_tick": 10}
        service._handle_joystick(ControlEnvelope(7, JoystickState(1, True, False)))
        self.assertEqual(responses, [])
        self.assertIn(1, service.pending)
        first_command = decode_frame(service.engine_control.sent[0][4:])
        self.assertEqual(first_command["target_world_tick"], 14)
        self.assertNotIn("episode", first_command)
        self.assertNotIn("target_tick", first_command)
        service._handle_engine_ack({"type": "action_ack", "sequence": 1, "status": "accepted"})
        self.assertEqual(responses[0][0], 7)
        self.assertEqual(responses[0][1]["status"], "accepted")
        service._handle_joystick(ControlEnvelope(8, JoystickState(2, True, False)))
        self.assertEqual(len(responses), 1)
        service._handle_engine_ack({"type": "action_ack", "sequence": 2, "status": "late"})
        self.assertEqual(responses[1][0], 8)
        self.assertEqual(responses[1][1]["status"], "rejected")

    def test_controller_does_not_claim_ready_without_engine_control(self):
        manifest = ControllerManifest("s", Endpoint("127.0.0.1", 1),
                                     Endpoint("127.0.0.1", 2), Endpoint("127.0.0.1", 3))
        service = ControllerService(manifest, connect_timeout=0.05)
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = service.run()
        self.assertEqual(status, 1)
        self.assertNotIn("READY ", stdout.getvalue())
        self.assertIn("startup failed", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
