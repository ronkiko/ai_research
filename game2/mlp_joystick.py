"""Scheduled real-time actions, with expiry and no access to the world state."""
from controls import Action
from protocol import MAX_FUTURE, RESET, decode_command


class MlpJoystick:
    def __init__(self, transport):
        self.transport = transport
        self.generation = -1
        self.connected = False
        self.accepted = self.late = self.rejected = self.last_sequence = 0
        self.reset()

    def reset(self):
        self.pending = {}
        self.right = False
        self.expires = 0

    def poll(self, episode, tick):
        generation, connected, packets = self.transport.drain()
        self.connected = connected
        if generation != self.generation:
            self.reset()
            self.generation = generation
            self.accepted = self.last_sequence = 0
        if not connected:
            return None
        for data in packets:
            try:
                command = decode_command(data)
            except ValueError:
                self.rejected += 1
                continue
            if command.sequence <= self.last_sequence:
                self.rejected += 1
                continue
            self.last_sequence = command.sequence
            if command.episode != episode:
                self.rejected += 1
                continue
            if command.op == RESET:
                self.accepted = command.sequence
                return 'reset'  # Any remaining old-episode commands are discarded.
            if command.target_tick <= tick:
                self.late += 1
                continue
            if command.target_tick > tick + MAX_FUTURE:
                self.rejected += 1
                continue
            # Most recently accepted command wins when targeting the same tick.
            self.pending[command.target_tick] = command
            self.accepted = command.sequence
        return None

    def next_action(self, tick):
        if tick >= self.expires:
            self.right = False
        command = self.pending.pop(tick, None)
        if command is not None:
            self.right = command.right
            self.expires = tick + command.hold_ticks
            return Action(self.right, command.jump)
        return Action(self.right, False)

    def close(self):
        self.reset()
