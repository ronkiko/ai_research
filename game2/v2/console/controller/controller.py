"""Game-console input subsystem: Joystick decisions to private Engine commands."""
from __future__ import annotations

import json
import socket
import sys
import threading
import time

from ..config import ControllerManifest
from ...contracts.framing import encode_frame, recv_frame
from ...contracts.joystick import JoystickState, decode_joystick_message, joystick_ack
from ..protocol import ActionCommand, action_message
from ..transport.control_server import ControlEnvelope, ControlServer


def _connect(endpoint, timeout=5.0):
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


class ControllerService:
    """Owns the Joystick port and the only Engine CONTROL client."""

    def __init__(self, manifest: ControllerManifest,
                 hold_ticks: int = 1, lead_ticks: int = 4, connect_timeout: float = 5.0):
        if hold_ticks < 1 or lead_ticks < 1:
            raise ValueError("controller timing values must be positive")
        self.manifest = manifest
        self.hold_ticks = hold_ticks
        self.lead_ticks = lead_ticks
        self.connect_timeout = connect_timeout
        self.engine_control: socket.socket | None = None
        self.telemetry: socket.socket | None = None
        self.joystick = ControlServer(
            manifest.joystick.host, manifest.joystick.port,
            decoder=decode_joystick_message,
        )
        self.latest: dict | None = None
        self.next_target = 0
        self.last_sequence = 0
        self.sequences: set[int] = set()
        self.pending: dict[int, tuple[int, int]] = {}
        self.lock = threading.Lock()
        self.engine_closed = threading.Event()
        self.telemetry_ready = threading.Event()
        self.telemetry_thread: threading.Thread | None = None
        self.ack_thread: threading.Thread | None = None

    def start(self) -> None:
        self.joystick.start()
        try:
            self.engine_control = _connect(self.manifest.engine_control, self.connect_timeout)
            self.telemetry = _connect(self.manifest.engine_telemetry, self.connect_timeout)
            self.telemetry_thread = threading.Thread(
                target=self._telemetry_loop, name="v2-controller-telemetry", daemon=True)
            self.telemetry_thread.start()
            self.ack_thread = threading.Thread(
                target=self._engine_ack_loop, name="v2-controller-engine-acks", daemon=True)
            self.ack_thread.start()
            if not self.telemetry_ready.wait(timeout=5) or self.engine_closed.is_set():
                raise RuntimeError("Engine TELEMETRY produced no scheduling snapshot")
        except (OSError, RuntimeError):
            self.close()
            raise

    def _telemetry_loop(self) -> None:
        assert self.telemetry is not None
        try:
            while True:
                try:
                    message = recv_frame(self.telemetry)
                except socket.timeout:
                    continue
                if message.get("type") != "telemetry":
                    continue
                with self.lock:
                    previous_episode = self.latest.get("episode") if self.latest else None
                    self.latest = message
                    if message.get("episode") != previous_episode:
                        self.next_target = 0
                    self.telemetry_ready.set()
        except (EOFError, OSError, ValueError):
            self.engine_closed.set()
        finally:
            if self.telemetry:
                self.telemetry.close()

    def _engine_ack_loop(self) -> None:
        assert self.engine_control is not None
        try:
            while True:
                try:
                    message = recv_frame(self.engine_control)
                except socket.timeout:
                    continue
                if message.get("type") != "action_ack":
                    continue
                self._handle_engine_ack(message)
        except (EOFError, OSError, ValueError):
            self._reject_pending()
            self.engine_closed.set()

    def _handle_engine_ack(self, message: dict) -> None:
        sequence = message.get("sequence")
        if type(sequence) is not int:
            return
        with self.lock:
            pending = self.pending.pop(sequence, None)
        if pending is None:
            return
        client_id, joystick_sequence = pending
        joystick_status = {
            "accepted": "accepted",
            "duplicate": "duplicate",
            "late": "rejected",
            "rejected": "rejected",
        }.get(str(message.get("status")), "rejected")
        self.joystick.respond(
            client_id, joystick_ack(joystick_sequence, joystick_status))

    def _reject_pending(self) -> None:
        with self.lock:
            pending, self.pending = self.pending, {}
        for client_id, joystick_sequence in pending.values():
            self.joystick.respond(client_id, joystick_ack(joystick_sequence, "rejected"))

    def _handle_joystick(self, envelope: ControlEnvelope) -> None:
        state = envelope.command
        if not isinstance(state, JoystickState):
            self.joystick.respond(envelope.client_id, joystick_ack(0, "rejected"))
            return

        with self.lock:
            if state.sequence <= self.last_sequence:
                status = "duplicate" if state.sequence in self.sequences else "rejected"
                response = joystick_ack(state.sequence, status)
            elif self.latest is None or self.engine_closed.is_set():
                self.last_sequence = state.sequence
                self.sequences.add(state.sequence)
                response = joystick_ack(state.sequence, "rejected")
            else:
                self.last_sequence = state.sequence
                self.sequences.add(state.sequence)
                current_world_tick = int(self.latest["world_tick"])
                target_world_tick = max(current_world_tick + self.lead_ticks,
                                        self.next_target + 1)
                self.next_target = target_world_tick + self.hold_ticks - 1
                command = ActionCommand(state.sequence, target_world_tick, self.hold_ticks,
                                         state.right, state.jump)
                try:
                    assert self.engine_control is not None
                    self.pending[command.sequence] = (envelope.client_id, state.sequence)
                    self.engine_control.sendall(encode_frame(action_message(command)))
                    return
                except (AssertionError, OSError):
                    self.pending.pop(command.sequence, None)
                    response = joystick_ack(state.sequence, "rejected")
        self.joystick.respond(envelope.client_id, response)

    def run(self) -> int:
        try:
            self.start()
        except (OSError, RuntimeError) as exc:
            print(f"ERROR Controller startup failed: {exc}", file=sys.stderr, flush=True)
            return 1
        print("READY " + json.dumps({"session_id": self.manifest.session_id,
                                     "joystick": self.manifest.joystick.as_dict()}, sort_keys=True),
              flush=True)
        try:
            while not self.engine_closed.is_set():
                for envelope in self.joystick.drain():
                    self._handle_joystick(envelope)
                time.sleep(0.001)
        finally:
            self.close()
        return 0

    def close(self) -> None:
        self.engine_closed.set()
        self._reject_pending()
        self.joystick.close()
        for sock in (self.telemetry, self.engine_control):
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass
        for thread in (self.telemetry_thread, self.ack_thread):
            if thread and thread is not threading.current_thread():
                thread.join(timeout=1)
