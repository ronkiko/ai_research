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

from game2.v2.console.config import (ControllerManifest, DisplayManifest,
                                      EngineManifest, InternalManifest, SessionConfig,
                                      allocate_endpoint)
from game2.v2.console.controller.controller import ControllerService
from game2.v2.console.display.display import DisplayService
from game2.v2.console.engine.engine import Engine, EngineService
from game2.v2.console.engine.map_loader import load_map
from game2.v2.console.engine.physics import PhysicsConfig
from game2.v2.console.main import run_session
from game2.v2.console.protocol import ActionCommand, action_message
from game2.v2.console.transport.control_server import ControlEnvelope, ControlServer
from game2.v2.console.transport.publisher import EventPublisher, LatestPublisher
from game2.v2.contracts.framing import ProtocolError, encode_frame, recv_frame
from game2.v2.contracts.joystick import (JoystickState, decode_joystick_message,
                                          joystick_ack, joystick_message)
from game2.v2.contracts.manifests import Endpoint, PeripheralManifest
from game2.v2.tests.harness import run_realtime_smoke

ROOT = Path(__file__).resolve().parents[3]
V2 = ROOT / "game2" / "v2"
PIT = ROOT / "game2" / "maps" / "pit.json"


def _module_name(path: Path, package_root: Path = V2) -> str:
    relative = path.relative_to(package_root)
    parts = relative.with_suffix("").parts
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(("game2", "v2", *parts))


def _imports_from_tree(tree: ast.AST, module_name: str, package_name: str | None = None) -> set[str]:
    modules = set()
    package = package_name or module_name.rsplit(".", 1)[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module:
                    modules.add(node.module)
                continue

            package_parts = package.split(".")
            ascend = node.level - 1
            if ascend > len(package_parts):
                continue
            base = ".".join(package_parts[:len(package_parts) - ascend])
            if node.module:
                modules.add(".".join(part for part in (base, node.module) if part))
            else:
                modules.add(base)
                modules.update(f"{base}.{alias.name}" for alias in node.names if alias.name != "*")
    return modules


def _absolute_imports(path: Path, package_root: Path = V2) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module_name = _module_name(path, package_root)
    package_name = module_name if path.name == "__init__.py" else None
    return _imports_from_tree(tree, module_name, package_name)


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
            "player/scripted", "tests",
        ):
            directory = V2 / relative
            self.assertTrue((directory / "README.md").is_file(), relative)
            self.assertTrue((directory / "doc").is_dir(), relative)

    def test_relocated_entrypoints_and_flat_modules(self):
        for relative in (
            "console/main.py", "console/engine/main.py", "console/controller/main.py",
            "console/display/main.py", "player/scripted/main.py", "management/main.py",
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
                imported = _absolute_imports(source)
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
        imported = _imports_from_tree(player_tree, "game2.v2.player.foo")
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
            imported = _imports_from_tree(ast.parse(source), "game2.v2.management.foo")
            self.assertIn("game2.v2.console.main", imported)
            leaked = [module for module in imported
                      if any(module == prefix or module.startswith(prefix + ".")
                             for prefix in denied)]
            self.assertEqual(leaked, ["game2.v2.console.main"])

    def test_scripted_player_imports_only_public_v2_modules(self):
        imported = _absolute_imports(V2 / "player" / "scripted" / "main.py")
        v2_imports = {module for module in imported if module.startswith("game2.v2.")}
        self.assertTrue(v2_imports)
        self.assertTrue(all(module.startswith("game2.v2.contracts") for module in v2_imports))


class ManifestAndWorldTests(unittest.TestCase):
    def test_internal_and_peripheral_manifests_are_separate(self):
        internal = InternalManifest("session", Endpoint("127.0.0.1", 12345),
                                   Endpoint("127.0.0.1", 12346), None, None, "/tmp/run")
        peripheral = PeripheralManifest("session", Endpoint("127.0.0.1", 12347))
        self.assertEqual(InternalManifest.from_dict(internal.to_dict()), internal)
        self.assertEqual(PeripheralManifest.from_dict(peripheral.to_dict()), peripheral)
        self.assertEqual(set(peripheral.to_dict()), {"session_id", "joystick"})
        self.assertNotIn("engine_control", peripheral.to_dict())
        self.assertNotIn("engine_state", peripheral.to_dict())
        self.assertNotIn("engine_telemetry", peripheral.to_dict())
        self.assertNotIn("engine_events", peripheral.to_dict())

    def test_manifest_rejects_engine_leakage(self):
        with self.assertRaises(ValueError):
            PeripheralManifest.from_dict({
                "session_id": "s", "joystick": {"host": "127.0.0.1", "port": 1},
                 "engine_control": {"host": "127.0.0.1", "port": 2},
            })

    def test_subsystem_manifests_have_only_required_capabilities(self):
        control = Endpoint("127.0.0.1", 12345)
        telemetry = Endpoint("127.0.0.1", 12346)
        state = Endpoint("127.0.0.1", 12347)
        joystick = Endpoint("127.0.0.1", 12348)
        controller = ControllerManifest("s", control, telemetry, joystick)
        display = DisplayManifest("s", state)
        engine = EngineManifest("s", control, state, telemetry, None, "/tmp/run")
        self.assertEqual(set(controller.to_dict()),
                         {"session_id", "engine_control", "engine_telemetry", "joystick"})
        self.assertEqual(set(display.to_dict()), {"session_id", "engine_state"})
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
        for reference in ("doc/REALTIME_SYSTEM.md", "ARCHITECTURE.md", "console/SPEC.md"):
            self.assertIn(reference, readme)

    def test_map_loader_and_fixed_world(self):
        loaded = load_map(PIT)
        self.assertEqual((loaded.width, loaded.height), (1280, 768))
        self.assertEqual(PhysicsConfig(hz=120).dt, 1 / 120)
        with self.assertRaises(ValueError):
            PhysicsConfig(hz=0)
        engine = Engine(loaded)
        self.assertIs(engine.avatar, engine.physics.body)
        engine.tick()
        self.assertEqual((engine.session_tick, engine.episode_tick), (1, 1))
        self.assertIsNot(engine.world_state().avatar, engine.avatar)

    def test_physical_state_is_immutable(self):
        state = Engine(load_map(PIT)).world_state()
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
        engine = Engine(load_map(PIT))
        self.assertEqual(engine.submit_action(ActionCommand(1, 1, 3, 2, True)), "accepted")
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
        engine = Engine(load_map(PIT))
        command = ActionCommand(1, 1, 3, 1)
        self.assertEqual(engine.submit_action(command), "accepted")
        self.assertEqual(engine.submit_action(command), "duplicate")
        engine.tick()
        engine.tick()
        engine.tick()
        self.assertEqual(engine.submit_action(ActionCommand(1, 2, 1, 1)), "late")

    def test_engine_service_runs_without_player(self):
        with tempfile.TemporaryDirectory() as run_dir:
            config = SessionConfig(map=str(PIT), clock_mode="unpaced", enable_state=False,
                                   enable_telemetry=False, enable_events=False, session_ticks=8)
            manifest = InternalManifest("no-player", allocate_endpoint(), None, None, None, run_dir)
            service = EngineService(
                Engine(load_map(PIT), session_id="no-player"),
                EngineManifest("no-player", manifest.engine_control, None, None, None, run_dir),
                config,
            )
            summary = service.run()
            self.assertEqual(summary["session_ticks"], 8)


class ChannelTests(unittest.TestCase):
    def test_partial_tcp_reads_and_malformed_packet(self):
        left, right = socket.socketpair()
        try:
            payload = encode_frame({"version": 1, "type": "state", "tick": 4})
            left.sendall(payload[:2])
            left.sendall(payload[2:])
            self.assertEqual(recv_frame(right)["tick"], 4)
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
            for tick in range(1000):
                publisher.publish({"version": 1, "type": "state", "tick": tick})
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
        self.assertNotIn("target_tick", player)
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
        display = DisplayManifest("s", Endpoint("127.0.0.1", 4))
        self.assertEqual(set(controller.to_dict()),
                         {"session_id", "engine_control", "engine_telemetry", "joystick"})
        self.assertEqual(set(display.to_dict()), {"session_id", "engine_state"})
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
            for enabled in (False, True):
                config = {**base, "enable_display": enabled}
                path = Path(directory) / ("display-on.json" if enabled else "display-off.json")
                path.write_text(json.dumps(config), encoding="utf-8")
                paths.append(path)
            off_status, off = run_session(paths[0])
            on_status, on = run_session(paths[1])
        self.assertEqual((off_status, on_status), (0, 0))
        self.assertEqual(off["avatar"], on["avatar"])

    def test_display_has_no_public_video_payload(self):
        service = DisplayService(DisplayManifest("s", Endpoint("127.0.0.1", 12345)))
        self.assertFalse(hasattr(service, "output"))
        self.assertFalse(hasattr(service, "publish"))
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
        service.latest = {"episode": 1, "episode_tick": 10}
        service._handle_joystick(ControlEnvelope(7, JoystickState(1, True, False)))
        self.assertEqual(responses, [])
        self.assertIn(1, service.pending)
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
