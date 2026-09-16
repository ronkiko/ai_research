"""Bounded single-client TCP transport. Socket I/O never runs in the physics loop."""
import select
import socket
import threading
from collections import deque

from protocol import MAX_COMMAND, PREFIX, packet


class SocketTransport:
    def __init__(self, host='127.0.0.1', port=8765):
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.listener.bind((host, port))
            self.listener.listen(1)
            self.listener.setblocking(False)
        except BaseException:
            self.listener.close()
            raise
        self.address = self.listener.getsockname()
        self._condition = threading.Condition()
        self._lock = self._condition
        self._inbox = deque()
        self._latest = None
        self._queued_outgoing = deque()
        self._generation = 0
        self._connected = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name='game2-socket', daemon=True)
        self._thread.start()

    def drain(self):
        with self._condition:
            result = (self._generation, self._connected, list(self._inbox))
            self._inbox.clear()
            return result

    def wait_for_event(self, generation):
        """Wait for a connection change or an incoming command."""
        with self._condition:
            self._condition.wait_for(lambda: self._generation != generation or self._inbox)

    def publish(self, payload):
        return self._publish(payload, queued=False)

    def publish_queued(self, payload):
        """Queue an auto observation without changing realtime latest-frame behavior."""
        return self._publish(payload, queued=True)

    def _publish(self, payload, *, queued):
        with self._condition:
            if self._connected:
                if queued:
                    if len(self._queued_outgoing) >= 256:
                        raise ConnectionError('Observation queue overflow')
                    self._queued_outgoing.append(packet(payload))
                else:
                    # Realtime keeps only the newest unsent observation.
                    self._latest = packet(payload)

    def _connection(self, connected):
        with self._condition:
            self._generation += 1
            self._connected = connected
            self._inbox.clear()
            self._latest = None
            self._queued_outgoing.clear()
            self._condition.notify_all()

    def _run(self):
        peer = None
        incoming = bytearray()
        outgoing = b''
        sent = 0
        send_started = 0.0
        import time
        try:
            while not self._stop.is_set():
                if peer is not None and not outgoing:
                    with self._condition:
                        if self._queued_outgoing:
                            outgoing = self._queued_outgoing.popleft()
                        else:
                            outgoing, self._latest = self._latest or b'', None
                    sent = 0
                    send_started = time.monotonic()
                readers = [self.listener] + ([peer] if peer is not None else [])
                writers = [peer] if peer is not None and outgoing else []
                ready, writable, _ = select.select(readers, writers, [], 0.01)
                if self.listener in ready:
                    candidate, _ = self.listener.accept()
                    if peer is not None:
                        candidate.close()
                    else:
                        peer = candidate
                        peer.setblocking(False)
                        peer.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                        incoming.clear()
                        outgoing, sent = b'', 0
                        self._connection(True)
                if peer is None:
                    continue
                try:
                    if peer in ready:
                        chunk = peer.recv(4096)
                        if not chunk:
                            raise ConnectionError('Disconnected')
                        incoming.extend(chunk)
                        while len(incoming) >= PREFIX.size:
                            size = PREFIX.unpack_from(incoming)[0]
                            if not 1 <= size <= MAX_COMMAND:
                                raise ConnectionError('Invalid command length')
                            if len(incoming) < PREFIX.size + size:
                                break
                            payload = bytes(incoming[PREFIX.size:PREFIX.size + size])
                            del incoming[:PREFIX.size + size]
                            with self._condition:
                                if len(self._inbox) >= 256:
                                    raise ConnectionError('Command queue overflow')
                                self._inbox.append(payload)
                                self._condition.notify_all()
                    if peer in writable:
                        count = peer.send(memoryview(outgoing)[sent:])
                        if not count:
                            raise ConnectionError('Disconnected')
                        sent += count
                        if sent == len(outgoing):
                            outgoing = b''
                    if outgoing and time.monotonic() - send_started > 0.5:
                        raise ConnectionError('Slow reader')
                except BlockingIOError:
                    pass
                except (OSError, ConnectionError):
                    peer.close()
                    peer = None
                    outgoing = b''
                    self._connection(False)
        finally:
            if peer is not None:
                peer.close()
            self.listener.close()
            self._connection(False)

    def close(self):
        self._stop.set()
        with self._condition:
            self._condition.notify_all()
        self._thread.join(timeout=2)
