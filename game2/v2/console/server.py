"""Long-lived Console server composition for dynamic Player connections."""
from __future__ import annotations

import importlib
import json
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO, cast

from ..contracts.connection import (ATTACH, DETACH, PROBE, RESPAWN, START,
                                    decode_connection_message, lifecycle_ack,
                                    player_event, probe_response,
                                    validate_probe_response)
from ..contracts.discovery import (CURRENT_CONSOLE_PATH, ConsoleDiscovery,
                                   publish_current_console,
                                   remove_current_console)
from ..contracts.framing import encode_frame, recv_frame, send_frame
from ..contracts.manifests import Endpoint, PlayerManifest
from ..contracts.screen import (CURRENT_SCREEN_SOURCE_PATH, ScreenSourceDiscovery,
                                publish_screen_source, remove_screen_source)
from .config import (ControllerManifest, DisplayManifest, EngineManifest,
                     InternalManifest, ProprioceptionSourceManifest,
                     ScreenSourceManifest, SessionConfig,
                     allocate_endpoint, new_session_id)
from .protocol import (despawn_message, respawn_message, spawn_message)
from .transport.control_server import ControlServer
from .world import WorldDefinition, load_world


CONTROLLER_MODULES = {"default": "game2.v2.console.controller.main"}
DISPLAY_MODULE = "game2.v2.console.display.main"
VISION_RENDERER_MODULE = "game2.v2.console.display.vision.renderer"
SCREEN_SOURCE_MODULE = "game2.v2.console.display.screen.source"
PROPRIOCEPTION_SOURCE_MODULE = "game2.v2.console.proprioception.source"


def preflight(config_path: str | Path) -> tuple[SessionConfig, WorldDefinition]:
    """Validate server prerequisites before allocating or starting a clock."""
    path = Path(config_path).resolve()
    config = SessionConfig.from_file(path)
    if not config.enable_state or not config.enable_telemetry or not config.enable_events:
        raise ValueError("server mode requires STATE, TELEMETRY, and EVENTS")
    controller_module = CONTROLLER_MODULES.get(config.controller)
    if controller_module is None:
        raise ValueError(f"Unknown controller subsystem: {config.controller}")
    for module_name in (
            "game2.v2.console.engine.main", controller_module, DISPLAY_MODULE,
            VISION_RENDERER_MODULE, SCREEN_SOURCE_MODULE,
            PROPRIOCEPTION_SOURCE_MODULE):
        module = importlib.import_module(module_name)
        if not callable(getattr(module, "main", None)) and module_name != VISION_RENDERER_MODULE:
            raise ValueError(f"Required subsystem entrypoint is missing: {module_name}")
    world = load_world(config.map_path(path))
    return config, world


def _connect(endpoint: Endpoint, timeout: float = 5.0) -> socket.socket:
    deadline = time.monotonic() + timeout
    while True:
        try:
            sock = socket.create_connection((endpoint.host, endpoint.port), timeout=1)
            sock.settimeout(0.25)
            return sock
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


def _terminate(process: subprocess.Popen | None) -> None:
    if process is None:
        return
    if process.poll() is not None:
        process.wait()
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _pump(source, destination) -> None:
    try:
        for line in source:
            try:
                destination.write(line)
                destination.flush()
            except (OSError, ValueError):
                return
    finally:
        source.close()


def _launch_ready(command: list[str], root: str, log, label: str) -> subprocess.Popen:
    process = subprocess.Popen(command, cwd=root, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, bufsize=1)
    ready_line = ""
    if process.stdout is not None:
        ready_deadline = time.monotonic() + 5
        while time.monotonic() < ready_deadline:
            remaining = ready_deadline - time.monotonic()
            readable, _, _ = select.select([process.stdout], [], [], remaining)
            if readable:
                ready_line = process.stdout.readline()
                break
    log.write(ready_line)
    log.flush()
    if not ready_line.startswith("READY "):
        _terminate(process)
        if process.stdout:
            process.stdout.close()
        raise RuntimeError(f"{label} exited or failed before READY")
    threading.Thread(target=_pump, args=(process.stdout, log),
                     name=f"v2-server-{label.lower()}-output", daemon=True).start()
    return process


class EngineLifecycleClient:
    """Synchronous, private Console-to-Engine lifecycle control connection."""

    def __init__(self, endpoint: Endpoint):
        self.endpoint = endpoint
        self.socket: socket.socket | None = None
        self.lock = threading.Lock()

    def connect(self) -> None:
        self.socket = _connect(self.endpoint)

    def request(self, payload: dict, expected_type: str) -> dict:
        with self.lock:
            if self.socket is None:
                raise ConnectionError("Engine lifecycle connection is closed")
            try:
                send_frame(self.socket, payload)
                deadline = time.monotonic() + 5
                while True:
                    try:
                        message = recv_frame(self.socket)
                    except socket.timeout:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Engine lifecycle acknowledgement timed out")
                        continue
                    if message.get("type") == expected_type:
                        return message
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Engine lifecycle acknowledgement timed out")
            except (EOFError, OSError, ValueError) as exc:
                raise ConnectionError("Engine lifecycle connection failed") from exc

    def close(self) -> None:
        if self.socket is None:
            return
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.socket.close()
        except OSError:
            pass
        self.socket = None


@dataclass
class PlayerRuntime:
    connection_id: int
    player_id: str
    actor_id: str
    manifest: PlayerManifest
    directory: Path
    controller: subprocess.Popen
    display: subprocess.Popen
    proprioception: subprocess.Popen
    logs: tuple[TextIO, TextIO, TextIO]
    spawned: bool = False


class ConsoleServer:
    """Own the attach listener and the independent services of each Player."""

    def __init__(self, session_id: str, world: WorldDefinition, world_file: Path, run_dir: Path,
                 engine: EngineLifecycleClient, controller_name: str,
                 engine_state: Endpoint, engine_telemetry: Endpoint,
                 engine_events: Endpoint):
        self.session_id = session_id
        self.world = world
        self.world_file = world_file
        self.run_dir = run_dir
        self.engine = engine
        self.controller_name = controller_name
        self.engine_state = engine_state
        self.engine_telemetry = engine_telemetry
        self.engine_events = engine_events
        self.attach = ControlServer("127.0.0.1", 0,
                                    decoder=decode_connection_message,
                                    on_closed=self._connection_closed)
        self.connections: dict[int, PlayerRuntime] = {}
        self.pending: set[int] = set()
        self.closed_connections: set[int] = set()
        self.lock = threading.RLock()
        self.closing = threading.Event()
        self.event_socket: socket.socket | None = None
        self.event_thread: threading.Thread | None = None

    @property
    def endpoint(self) -> Endpoint:
        return Endpoint(self.attach.host, self.attach.port)

    def start(self) -> None:
        self.attach.start()
        self.event_socket = _connect(self.engine_events)
        self.event_thread = threading.Thread(target=self._event_loop,
                                             name="v2-console-engine-events", daemon=True)
        self.event_thread.start()

    def _connection_closed(self, connection_id: int) -> None:
        with self.lock:
            pending = connection_id in self.pending
            if pending:
                self.closed_connections.add(connection_id)
        if not pending:
            self._detach(connection_id, close_client=False)

    def _connection_error(self, connection_id: int, reason: str) -> None:
        self.attach.respond(connection_id, {
            "version": 1, "type": "connection_error", "error": reason})
        self.attach.close_client(connection_id)

    def _handle_command(self, connection_id: int, operation: str) -> None:
        if operation == PROBE:
            self.attach.respond(connection_id, probe_response(
                self.session_id, self.world.map_id, self.attach.host, self.attach.port))
            return
        if operation == ATTACH:
            self._attach(connection_id)
            return
        with self.lock:
            runtime = self.connections.get(connection_id)
        if runtime is None:
            self._connection_error(connection_id, "Player is not attached")
            return
        if operation == START:
            self._start(runtime)
        elif operation == RESPAWN:
            self._respawn(runtime)
        elif operation == DETACH:
            self._detach(connection_id, close_client=True)

    def drain_commands(self) -> None:
        for envelope in self.attach.drain():
            try:
                self._handle_command(envelope.client_id, cast(str, envelope.command))
            except (ConnectionError, OSError, RuntimeError, TimeoutError, ValueError) as exc:
                self._connection_error(envelope.client_id, str(exc))

    def _attach(self, connection_id: int) -> None:
        with self.lock:
            if connection_id in self.connections or connection_id in self.pending:
                self._connection_error(connection_id, "Player connection is already attached")
                return
            self.pending.add(connection_id)

        player_id = f"player-{new_session_id()[:16]}"
        actor_id = f"actor-{new_session_id()[:16]}"
        directory = self.run_dir / "players" / player_id
        controller = display = proprioception = None
        logs: list[TextIO] = []
        try:
            directory.mkdir(parents=True, exist_ok=False)
            controller_manifest = ControllerManifest(
                self.session_id,
                self.engine.endpoint,
                allocate_endpoint(),
                actor_id,
            )
            vision_endpoint = allocate_endpoint()
            proprioception_endpoint = allocate_endpoint()
            display_manifest = DisplayManifest(
                self.session_id, self.engine_state, str(self.world_file), "vision",
                vision_endpoint, actor_id)
            proprioception_manifest = ProprioceptionSourceManifest(
                self.session_id,
                self.engine_telemetry,
                proprioception_endpoint,
                actor_id,
            )
            controller_path = directory / "controller-manifest.json"
            display_path = directory / "display-manifest.json"
            proprioception_path = directory / "proprioception-manifest.json"
            controller_manifest.write(controller_path)
            display_manifest.write(display_path)
            proprioception_manifest.write(proprioception_path)
            controller_log = (directory / "controller.log").open("w", encoding="utf-8")
            display_log = (directory / "display.log").open("w", encoding="utf-8")
            proprioception_log = (
                directory / "proprioception.log"
            ).open("w", encoding="utf-8")
            logs = [controller_log, display_log, proprioception_log]
            root = str(Path(__file__).resolve().parents[3])
            controller = _launch_ready(
                [sys.executable, "-m", CONTROLLER_MODULES[self.controller_name],
                 "--manifest", str(controller_path)], root, controller_log, "Controller")
            display = _launch_ready(
                [sys.executable, "-m", DISPLAY_MODULE, "--manifest", str(display_path)],
                root, display_log, "Display")
            proprioception = _launch_ready(
                [sys.executable, "-m", PROPRIOCEPTION_SOURCE_MODULE,
                 "--manifest", str(proprioception_path)],
                root, proprioception_log, "Proprioception")
            manifest = PlayerManifest(
                self.session_id, player_id, actor_id,
                controller_manifest.joystick,
                vision_endpoint,
                proprioception_endpoint,
            )
            runtime = PlayerRuntime(
                connection_id, player_id, actor_id, manifest,
                directory, controller, display, proprioception,
                (controller_log, display_log, proprioception_log),
            )
            with self.lock:
                disconnected = connection_id in self.closed_connections or self.closing.is_set()
                self.pending.discard(connection_id)
                self.closed_connections.discard(connection_id)
                if not disconnected:
                    self.connections[connection_id] = runtime
            if disconnected:
                self._cleanup_runtime(runtime)
                return
            responded = self.attach.respond(connection_id, {
                "version": 1, "type": "player_manifest", **manifest.to_dict()})
            if not responded:
                with self.lock:
                    still_attached = self.connections.get(connection_id) is runtime
                    if still_attached:
                        self.connections.pop(connection_id, None)
                if still_attached:
                    self._cleanup_runtime(runtime)
                return
            threading.Thread(target=self._watch_player, args=(runtime,),
                             name="v2-player-watch", daemon=True).start()
        except BaseException:
            with self.lock:
                self.pending.discard(connection_id)
                self.closed_connections.discard(connection_id)
            _terminate(proprioception)
            _terminate(display)
            _terminate(controller)
            for log in logs:
                log.close()
            if directory.exists():
                shutil.rmtree(directory, ignore_errors=True)
            self.attach.close_client(connection_id)
            raise

    def _watch_player(self, runtime: PlayerRuntime) -> None:
        while not self.closing.is_set():
            with self.lock:
                if self.connections.get(runtime.connection_id) is not runtime:
                    return
            if (
                runtime.controller.poll() is not None
                or runtime.display.poll() is not None
                or runtime.proprioception.poll() is not None
            ):
                self._detach(runtime.connection_id, close_client=True)
                return
            time.sleep(0.05)

    def _start(self, runtime: PlayerRuntime) -> None:
        accepted = runtime.spawned
        world_tick = 0
        if not runtime.spawned:
            acknowledgement = self.engine.request(
                spawn_message(runtime.player_id, runtime.actor_id), "spawn_ack")
            accepted = acknowledgement.get("status") == "accepted"
            world_tick = acknowledgement.get("world_tick", 0)
            if accepted:
                runtime.spawned = True
        self.attach.respond(runtime.connection_id,
                            lifecycle_ack(START, "accepted" if accepted else "rejected",
                                          world_tick))

    def _respawn(self, runtime: PlayerRuntime) -> None:
        if not runtime.spawned:
            self.attach.respond(runtime.connection_id, lifecycle_ack(RESPAWN, "rejected", 0))
            return
        acknowledgement = self.engine.request(
            respawn_message(runtime.actor_id), "respawn_ack")
        accepted = acknowledgement.get("status") == "accepted"
        self.attach.respond(runtime.connection_id,
                            lifecycle_ack(RESPAWN, "accepted" if accepted else "rejected",
                                          acknowledgement.get("world_tick", 0)))

    def _detach(self, connection_id: int, close_client: bool) -> None:
        with self.lock:
            runtime = self.connections.pop(connection_id, None)
            self.pending.discard(connection_id)
        if runtime is None:
            if close_client:
                self.attach.close_client(connection_id)
            return
        if close_client:
            self.attach.respond(connection_id, {
                "version": 1, "type": "lifecycle_ack", "event": DETACH,
                "status": "accepted", "world_tick": 0})
        self._cleanup_runtime(runtime)
        if close_client:
            self.attach.close_client(connection_id)

    def _cleanup_runtime(self, runtime: PlayerRuntime) -> None:
        if runtime.spawned:
            try:
                self.engine.request(despawn_message(runtime.actor_id), "despawn_ack")
            except (ConnectionError, OSError, TimeoutError, ValueError):
                pass
            runtime.spawned = False
        _terminate(runtime.proprioception)
        _terminate(runtime.controller)
        _terminate(runtime.display)
        for log in runtime.logs:
            log.close()
        shutil.rmtree(runtime.directory, ignore_errors=True)

    def _event_loop(self) -> None:
        event_socket = self.event_socket
        if event_socket is None:
            return
        try:
            while not self.closing.is_set():
                try:
                    message = recv_frame(event_socket)
                except socket.timeout:
                    continue
                if message.get("type") != "event" or message.get("event") != "actor_finished":
                    continue
                actor_id = message.get("actor_id")
                result = message.get("result")
                world_tick = message.get("world_tick")
                if (type(actor_id) is not str or type(result) is not str or
                        type(world_tick) is not int):
                    continue
                with self.lock:
                    runtime = next((value for value in self.connections.values()
                                    if value.actor_id == actor_id), None)
                if runtime is not None:
                    self.attach.respond(runtime.connection_id,
                                        player_event(result, world_tick))
        except (EOFError, OSError, ValueError):
            return

    def close(self) -> None:
        if self.closing.is_set():
            return
        self.closing.set()
        self.attach.close()
        with self.lock:
            runtimes = list(self.connections.values())
            self.connections.clear()
            self.pending.clear()
        for runtime in runtimes:
            self._cleanup_runtime(runtime)
        if self.event_socket is not None:
            try:
                self.event_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self.event_socket.close()
            except OSError:
                pass
        if self.event_thread and self.event_thread is not threading.current_thread():
            self.event_thread.join(timeout=1)


def _probe_live(discovery: ConsoleDiscovery) -> bool:
    sock = None
    try:
        sock = socket.create_connection((discovery.attach.host, discovery.attach.port), timeout=0.5)
        sock.settimeout(0.5)
        send_frame(sock, {"version": 1, "type": PROBE})
        response = recv_frame(sock)
        validate_probe_response(response, discovery.session_id)
        return response["attach"] == discovery.attach.as_dict()
    except (OSError, EOFError, ValueError):
        return False
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def _existing_console_is_live(path: Path) -> bool:
    try:
        discovery = ConsoleDiscovery.from_file(path)
    except (FileNotFoundError, OSError, ValueError):
        return False
    return _probe_live(discovery)


def _check_discovery_directory(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".console-preflight-",
                                         delete=False) as target:
            temporary = Path(target.name)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def run_server(config_path: str | Path,
               discovery_path: str | Path = CURRENT_CONSOLE_PATH,
               screen_discovery_path: str | Path | None = None,
               screen_view: str = "screen",
               screen_episode_store: str | Path | None = None) -> int:
    """Run one persistent Console until explicit shutdown or process failure."""
    config_path = Path(config_path).resolve()
    discovery_path = Path(discovery_path)
    if screen_view not in {"screen", "vision"}:
        raise ValueError("screen_view must be screen or vision")
    if screen_episode_store is not None and screen_view != "vision":
        raise ValueError("screen_episode_store requires screen_view=vision")
    if screen_discovery_path is None:
        screen_discovery_path = (
            CURRENT_SCREEN_SOURCE_PATH
            if discovery_path == CURRENT_CONSOLE_PATH
            else discovery_path.with_name("screen-source.json")
        )
    screen_discovery_path = Path(screen_discovery_path)
    config, world = preflight(config_path)
    _check_discovery_directory(discovery_path)
    if discovery_path.exists() and _existing_console_is_live(discovery_path):
        print("Game2 V2 Console is already running", flush=True)
        return 0
    screen_discovery_path.unlink(missing_ok=True)

    session_id = new_session_id()
    run_dir = Path(__file__).resolve().parent / "runs" / session_id
    run_dir.mkdir(parents=True, exist_ok=False)
    internal = InternalManifest(session_id, allocate_endpoint(), allocate_endpoint(),
                                allocate_endpoint(), allocate_endpoint(), str(run_dir))
    internal.write(run_dir / "internal-manifest.json")
    engine_manifest_path = run_dir / "engine-manifest.json"
    EngineManifest(session_id, internal.engine_control, internal.engine_state,
                   internal.engine_telemetry, internal.engine_events, internal.run_dir,
                   None, None).write(engine_manifest_path)
    assert internal.engine_state is not None
    screen_endpoint = allocate_endpoint()
    screen_manifest_path = run_dir / "screen-source-manifest.json"
    ScreenSourceManifest(
        session_id,
        internal.engine_state,
        str(config.map_path(config_path).resolve()),
        screen_endpoint,
        config.physics_hz,
        config.episode_limit,
        screen_view,
    ).write(screen_manifest_path)
    console_log = (run_dir / "console.log").open("w", encoding="utf-8")
    engine_log = (run_dir / "engine.log").open("w", encoding="utf-8")
    screen_log = (run_dir / "screen-source.log").open("w", encoding="utf-8")
    engine = None
    screen_source = None
    engine_control = None
    server = None
    own_discovery: ConsoleDiscovery | None = None
    own_screen_source: ScreenSourceDiscovery | None = None
    interrupted = threading.Event()

    def stop(_signum, _frame):
        interrupted.set()

    previous_int = signal.signal(signal.SIGINT, stop)
    previous_term = signal.signal(signal.SIGTERM, stop)
    status = 1
    try:
        root = str(Path(__file__).resolve().parents[3])
        engine = _launch_ready(
            [sys.executable, "-m", "game2.v2.console.engine.main", "--config",
             str(config_path), "--manifest", str(engine_manifest_path)],
            root, engine_log, "Engine")
        engine_control = EngineLifecycleClient(internal.engine_control)
        engine_control.connect()
        screen_command = [
            sys.executable, "-m", SCREEN_SOURCE_MODULE,
            "--manifest", str(screen_manifest_path),
        ]
        if screen_episode_store is not None:
            screen_command.extend([
                "--episode-store", str(screen_episode_store),
            ])

        def start_screen_source():
            nonlocal own_screen_source
            process = _launch_ready(
                screen_command, root, screen_log, "ScreenSource")
            own_screen_source = ScreenSourceDiscovery(
                1, session_id, world.map_id, screen_endpoint,
                world.width, world.height)
            publish_screen_source(own_screen_source, screen_discovery_path)
            return process

        screen_retry_at = 0.0
        try:
            screen_source = start_screen_source()
        except (OSError, RuntimeError) as exc:
            screen_log.write(f"UNAVAILABLE {type(exc).__name__}: {exc}\n")
            screen_log.flush()
            screen_retry_at = time.monotonic() + 0.5
        assert internal.engine_state is not None
        assert internal.engine_telemetry is not None
        assert internal.engine_events is not None
        server = ConsoleServer(
            session_id,
            world,
            config.map_path(config_path),
            run_dir,
            engine_control,
            config.controller,
            internal.engine_state,
            internal.engine_telemetry,
            internal.engine_events,
        )
        server.start()
        own_discovery = ConsoleDiscovery(1, session_id, world.map_id, server.endpoint)
        publish_current_console(own_discovery, discovery_path)
        console_log.write(json.dumps(own_discovery.to_dict(), sort_keys=True) + "\n")
        console_log.flush()
        print("Game2 V2 Console", flush=True)
        print(f"World: {world.map_id}", flush=True)
        print(f"Physics: {config.physics_hz} Hz", flush=True)
        print("Players: 0", flush=True)
        print("Actors: 0", flush=True)
        print("Clock: running", flush=True)
        print(f"Attach: {server.endpoint.host}:{server.endpoint.port}", flush=True)
        print("READY " + json.dumps(own_discovery.to_dict(), sort_keys=True), flush=True)
        while not interrupted.is_set():
            if engine.poll() is not None:
                status = 0 if engine.returncode == 0 else 1
                break
            if screen_source is not None and screen_source.poll() is not None:
                returncode = screen_source.returncode
                if own_screen_source is not None:
                    remove_screen_source(own_screen_source, screen_discovery_path)
                    own_screen_source = None
                screen_source = None
                screen_retry_at = time.monotonic() + 0.25
                screen_log.write(
                    f"RESTART ScreenSource exited returncode={returncode}\n"
                )
                screen_log.flush()
            if (
                screen_source is None
                and not interrupted.is_set()
                and time.monotonic() >= screen_retry_at
            ):
                try:
                    screen_source = start_screen_source()
                    screen_log.write("RESTART ScreenSource ready\n")
                    screen_log.flush()
                except (OSError, RuntimeError) as exc:
                    screen_log.write(
                        f"RESTART failed {type(exc).__name__}: {exc}\n"
                    )
                    screen_log.flush()
                    screen_retry_at = time.monotonic() + 0.5
            server.drain_commands()
            time.sleep(0.005)
        if interrupted.is_set():
            status = 0
    except (OSError, RuntimeError, TimeoutError, ValueError, ConnectionError) as exc:
        console_log.write(f"ERROR {type(exc).__name__}: {exc}\n")
        console_log.flush()
        status = 1
    finally:
        if server is not None:
            server.close()
        if engine_control is not None:
            engine_control.close()
        _terminate(screen_source)
        _terminate(engine)
        if own_screen_source is not None:
            remove_screen_source(own_screen_source, screen_discovery_path)
        if own_discovery is not None:
            remove_current_console(own_discovery, discovery_path)
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)
        console_log.close()
        engine_log.close()
        screen_log.close()
    return status


__all__ = ["ConsoleServer", "EngineLifecycleClient", "preflight", "run_server"]
