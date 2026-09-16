"""Binary client adapter: continuously receive frames while the MLP computes."""
import socket
import threading
from collections import deque

from protocol import (ACTION, ACTION_PACKET, FEATURES, MAX_FRAME, PREFIX, RESET,
                      RESET_PACKET, decode_command, decode_features, decode_frame, packet)


class MLPClient:
    def __init__(self, host='127.0.0.1', port=8765, timeout=2):
        self.socket = socket.create_connection((host, port), timeout=timeout)
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.socket.settimeout(0.2)
        self.timeout = timeout
        self.sequence = 0
        self._condition = threading.Condition()
        self._send_lock = threading.Lock()
        self._latest = self._error = None
        self._features = deque()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._read, name='game2-observations', daemon=True)
        self._thread.start()

    def _read(self):
        incoming = bytearray()
        try:
            while not self._stop.is_set():
                try:
                    chunk = self.socket.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    raise ConnectionError('Game socket closed')
                incoming.extend(chunk)
                while len(incoming) >= PREFIX.size:
                    size = PREFIX.unpack_from(incoming)[0]
                    if not 1 <= size <= MAX_FRAME:
                        raise ValueError('Invalid frame length')
                    if len(incoming) < PREFIX.size + size:
                        break
                    payload = bytes(incoming[PREFIX.size:PREFIX.size + size])
                    if payload[0] == FEATURES:
                        frame = decode_features(payload)
                    else:
                        frame = decode_frame(payload)
                    del incoming[:PREFIX.size + size]
                    with self._condition:
                        if payload[0] == FEATURES:
                            self._features.append(frame)
                        else:
                            self._latest = frame  # Realtime remains bounded/latest.
                        self._condition.notify_all()
        except (OSError, ValueError) as error:
            with self._condition:
                self._error = error
                self._condition.notify_all()

    def receive(self) -> dict:
        """Receive the next compact observation or latest realtime frame."""
        with self._condition:
            ready = self._condition.wait_for(
                lambda: self._features or self._latest is not None or
                self._error is not None or self._stop.is_set(),
                timeout=self.timeout)
            if self._error is not None:
                raise self._error
            if self._stop.is_set():
                raise ConnectionError('Client closed')
            if not ready:
                raise TimeoutError('No observation received')
            if self._features:
                frame = self._features.popleft()
            else:
                frame, self._latest = self._latest, None
            if frame is None:
                raise ConnectionError('Observation stream ended')
            return frame

    def action(self, *, episode, target_tick, hold_ticks=4, right=False, jump=False):
        if type(right) is not bool or type(jump) is not bool:
            raise ValueError('Buttons must be boolean')
        with self._send_lock:
            payload = ACTION_PACKET.pack(ACTION, self.sequence + 1, episode,
                                         target_tick, hold_ticks, right, jump)
            decode_command(payload)
            self.socket.sendall(packet(payload))
            self.sequence += 1
            return self.sequence

    def reset(self, episode):
        with self._send_lock:
            payload = RESET_PACKET.pack(RESET, self.sequence + 1, episode)
            decode_command(payload)
            self.socket.sendall(packet(payload))
            self.sequence += 1
            return self.sequence

    def close(self):
        if self._stop.is_set():
            return
        self._stop.set()
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.socket.close()
        self._thread.join(timeout=2)
        with self._condition:
            self._condition.notify_all()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
