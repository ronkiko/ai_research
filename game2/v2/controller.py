"""Game-console input subsystem: Joystick decisions to private Engine commands."""
from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from game2.v2.config import InternalManifest, PeripheralManifest
from game2.v2.joystick import JoystickState, decode_joystick_message, joystick_ack
from game2.v2.protocol import ActionCommand, action_message, encode_frame, recv_frame
from game2.v2.transport.control_server import ControlEnvelope, ControlServer


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

    def __init__(self, internal: InternalManifest, peripheral: PeripheralManifest,
                 hold_ticks: int = 1, lead_ticks: int = 4):
        if hold_ticks < 1 or lead_ticks < 1:
            raise ValueError("controller timing values must be positive")
        self.internal = internal
        self.peripheral = peripheral
        self.hold_ticks = hold_ticks
        self.lead_ticks = lead_ticks
        self.engine_control: socket.socket | None = None
        self.telemetry: socket.socket | None = None
        self.joystick = ControlServer(
            peripheral.joystick.host, peripheral.joystick.port,
            decoder=decode_joystick_message,
        )
        self.latest = {"episode": 1, "episode_tick": 0}
        self.next_target = 0
        self.last_sequence = 0
        self.sequences: set[int] = set()
        self.lock = threading.Lock()
        self.engine_closed = threading.Event()
        self.telemetry_thread: threading.Thread | None = None
        self.ack_thread: threading.Thread | None = None

    def start(self) -> None:
        self.joystick.start()
        try:
            # Unpaced Engine may already have completed before this process is
            # spawned. That is valid world semantics, not a hidden pause.
            self.engine_control = _connect(self.internal.engine_control, timeout=0.5)
        except OSError:
            self.engine_closed.set()
            return
        if self.internal.engine_telemetry:
            try:
                self.telemetry = _connect(self.internal.engine_telemetry)
                self.telemetry_thread = threading.Thread(target=self._telemetry_loop,
                                                         name="v2-controller-telemetry", daemon=True)
                self.telemetry_thread.start()
            except OSError:
                self.telemetry = None
        self.ack_thread = threading.Thread(target=self._engine_ack_loop,
                                           name="v2-controller-engine-acks", daemon=True)
        self.ack_thread.start()

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
                    previous_episode = self.latest.get("episode")
                    self.latest = message
                    if message.get("episode") != previous_episode:
                        self.next_target = 0
        except (EOFError, OSError, socket.timeout, ValueError):
            pass
        finally:
            if self.telemetry:
                self.telemetry.close()

    def _engine_ack_loop(self) -> None:
        assert self.engine_control is not None
        try:
            while True:
                try:
                    recv_frame(self.engine_control)
                except socket.timeout:
                    continue
        except (EOFError, OSError, ValueError):
            self.engine_closed.set()

    def _handle_joystick(self, envelope: ControlEnvelope) -> None:
        state = envelope.command
        if not isinstance(state, JoystickState):
            self.joystick.respond(envelope.client_id, joystick_ack(0, "rejected"))
            return
        with self.lock:
            if state.sequence <= self.last_sequence:
                status = "duplicate" if state.sequence in self.sequences else "rejected"
                self.joystick.respond(envelope.client_id, joystick_ack(state.sequence, status))
                return
            self.last_sequence = state.sequence
            self.sequences.add(state.sequence)
            episode = int(self.latest.get("episode", 1))
            current_tick = int(self.latest.get("episode_tick", 0))
            target_tick = max(current_tick + self.lead_ticks, self.next_target + 1)
            self.next_target = target_tick + self.hold_ticks - 1
            command = ActionCommand(episode, state.sequence, target_tick, self.hold_ticks,
                                    state.right, state.jump)
            try:
                assert self.engine_control is not None
                self.engine_control.sendall(encode_frame(action_message(command)))
                status = "accepted"
            except (AssertionError, OSError):
                status = "rejected"
        self.joystick.respond(envelope.client_id, joystick_ack(state.sequence, status))

    def run(self) -> None:
        self.start()
        print("READY " + json.dumps({"session_id": self.internal.session_id,
                                     "joystick": self.peripheral.joystick.as_dict()}, sort_keys=True),
              flush=True)
        try:
            while not self.engine_closed.is_set():
                for envelope in self.joystick.drain():
                    self._handle_joystick(envelope)
                time.sleep(0.001)
        finally:
            self.close()

    def close(self) -> None:
        self.engine_closed.set()
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Game2 V2 Controller subsystem")
    parser.add_argument("--internal-manifest", required=True)
    parser.add_argument("--peripheral-manifest", required=True)
    parser.add_argument("--hold-ticks", type=int, default=1)
    parser.add_argument("--lead-ticks", type=int, default=4)
    args = parser.parse_args(argv)
    service = ControllerService(InternalManifest.from_file(args.internal_manifest),
                                PeripheralManifest.from_file(args.peripheral_manifest),
                                args.hold_ticks, args.lead_ticks)
    service.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
