"""Realtime Console input bridge: public Joystick state -> Engine input latch."""
from __future__ import annotations

import json
import socket
import sys
import threading
import time

from ..config import ControllerManifest
from ...contracts.framing import encode_frame, recv_frame
from ...contracts.joystick import JoystickState, decode_joystick_message, joystick_ack
from ..protocol import InputStateCommand, input_state_message
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
    """Translate complete Joystick states without scheduling future input.

    The Controller never decides how long a button is held. A state accepted by
    Engine stays latched there until the next accepted state replaces it.
    """

    def __init__(self, manifest: ControllerManifest, connect_timeout: float = 5.0):
        self.manifest = manifest
        self.connect_timeout = connect_timeout
        self.engine_control: socket.socket | None = None
        self.joystick = ControlServer(
            manifest.joystick.host,
            manifest.joystick.port,
            decoder=decode_joystick_message,
            on_closed=self._joystick_closed,
        )
        self.public_last_sequence = 0
        self.private_sequence = 0
        self.pending: dict[int, tuple[int, int] | None] = {}
        self.active_client_id: int | None = None
        self.lock = threading.RLock()
        self.engine_closed = threading.Event()
        self.closing = threading.Event()
        self.ack_thread: threading.Thread | None = None

    def start(self) -> None:
        self.joystick.start()
        try:
            self.engine_control = _connect(
                self.manifest.engine_control, self.connect_timeout
            )
            self.ack_thread = threading.Thread(
                target=self._engine_ack_loop,
                name="v2-controller-engine-acks",
                daemon=True,
            )
            self.ack_thread.start()
        except OSError:
            self.close()
            raise

    def _send_private_state(
        self,
        right: bool,
        jump: bool,
        pending: tuple[int, int] | None,
    ) -> int:
        if self.engine_control is None or self.engine_closed.is_set():
            raise ConnectionError("Engine CONTROL is unavailable")
        self.private_sequence += 1
        command = InputStateCommand(
            self.manifest.actor_id,
            self.private_sequence,
            right,
            jump,
        )
        self.pending[command.sequence] = pending
        try:
            self.engine_control.sendall(encode_frame(input_state_message(command)))
        except OSError:
            self.pending.pop(command.sequence, None)
            self.engine_closed.set()
            raise
        return command.sequence

    def _neutralize_active(self) -> None:
        with self.lock:
            if self.active_client_id is None:
                return
            try:
                self._send_private_state(False, False, None)
            except (ConnectionError, OSError):
                pass
            self.active_client_id = None
            self.public_last_sequence = 0

    def _joystick_closed(self, client_id: int) -> None:
        if self.closing.is_set():
            return
        with self.lock:
            if self.active_client_id != client_id:
                return
        self._neutralize_active()

    def _engine_ack_loop(self) -> None:
        assert self.engine_control is not None
        try:
            while True:
                try:
                    message = recv_frame(self.engine_control)
                except socket.timeout:
                    continue
                if message.get("type") != "input_ack":
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
        status = message.get("status")
        joystick_status = status if status in {
            "accepted", "duplicate", "rejected"
        } else "rejected"
        self.joystick.respond(
            client_id,
            joystick_ack(joystick_sequence, joystick_status),
        )

    def _reject_pending(self) -> None:
        with self.lock:
            pending, self.pending = self.pending, {}
        for item in pending.values():
            if item is None:
                continue
            client_id, joystick_sequence = item
            self.joystick.respond(
                client_id,
                joystick_ack(joystick_sequence, "rejected"),
            )

    def _handle_joystick(self, envelope: ControlEnvelope) -> None:
        state = envelope.command
        if not isinstance(state, JoystickState):
            self.joystick.respond(
                envelope.client_id, joystick_ack(0, "rejected")
            )
            return

        response = None
        with self.lock:
            if (
                self.active_client_id is not None
                and self.active_client_id != envelope.client_id
            ):
                response = joystick_ack(state.sequence, "rejected")
            elif state.sequence <= self.public_last_sequence:
                status = (
                    "duplicate"
                    if state.sequence == self.public_last_sequence
                    else "rejected"
                )
                response = joystick_ack(state.sequence, status)
            elif self.engine_closed.is_set():
                response = joystick_ack(state.sequence, "rejected")
            else:
                self.active_client_id = envelope.client_id
                self.public_last_sequence = state.sequence
                try:
                    self._send_private_state(
                        state.right,
                        state.jump,
                        (envelope.client_id, state.sequence),
                    )
                    return
                except (ConnectionError, OSError):
                    response = joystick_ack(state.sequence, "rejected")
        if response is not None:
            self.joystick.respond(envelope.client_id, response)

    def run(self) -> int:
        try:
            self.start()
        except OSError as exc:
            print(
                f"ERROR Controller startup failed: {exc}",
                file=sys.stderr,
                flush=True,
            )
            return 1
        print(
            "READY " + json.dumps({
                "session_id": self.manifest.session_id,
                "joystick": self.manifest.joystick.as_dict(),
            }, sort_keys=True),
            flush=True,
        )
        try:
            while not self.engine_closed.is_set():
                for envelope in self.joystick.drain():
                    self._handle_joystick(envelope)
                time.sleep(0.001)
        finally:
            self.close()
        return 0

    def close(self) -> None:
        if self.closing.is_set():
            return
        self._neutralize_active()
        self.closing.set()
        self._reject_pending()
        self.joystick.close()
        if self.engine_control is not None:
            try:
                self.engine_control.close()
            except OSError:
                pass
            self.engine_control = None
        if (
            self.ack_thread
            and self.ack_thread is not threading.current_thread()
        ):
            self.ack_thread.join(timeout=1)


__all__ = ["ControllerService"]
