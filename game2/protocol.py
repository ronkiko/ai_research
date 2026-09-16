"""Version 2 binary socket protocol; all integers are network byte order."""
import struct
import zlib
from dataclasses import dataclass

VERSION = 2
ACTION, RESET, FRAME = 1, 2, 128
PREFIX = struct.Struct('!I')
ACTION_PACKET = struct.Struct('!BIIQHBB')
RESET_PACKET = struct.Struct('!BII')
# type, version, episode, tick, w, h, physics_hz, monitor_hz, status,
# last accepted action/reset sequence, late, rejected, overrun_ticks,
# jump_requested, jump_applied, event_sequence, last_event
# (0 none, 1 die, 2 success, 3 reset).
FRAME_HEADER = struct.Struct('!BBIQHHHHBIIIIIIIB')
MAX_COMMAND = 64
MAX_FRAME = 17 * 1024 * 1024
MAX_HOLD = MAX_FUTURE = 120


@dataclass(frozen=True)
class Command:
    op: int
    sequence: int
    episode: int
    target_tick: int = 0
    hold_ticks: int = 1
    right: bool = False
    jump: bool = False


def decode_command(data):
    if len(data) == ACTION_PACKET.size and data[0] == ACTION:
        op, seq, episode, tick, hold, right, jump = ACTION_PACKET.unpack(data)
        if not seq or not episode or not 1 <= hold <= MAX_HOLD or right > 1 or jump > 1:
            raise ValueError('Invalid action fields')
        return Command(op, seq, episode, tick, hold, bool(right), bool(jump))
    if len(data) == RESET_PACKET.size and data[0] == RESET:
        op, seq, episode = RESET_PACKET.unpack(data)
        if not seq or not episode:
            raise ValueError('Invalid reset fields')
        return Command(op, seq, episode)
    raise ValueError('Invalid command packet')


def packet(payload):
    return PREFIX.pack(len(payload)) + payload


def encode_frame(frame, *, episode, tick, hz, monitor_hz, status, accepted,
                 late, rejected, overrun_ticks, jump_requested, jump_applied,
                 event_sequence, last_event):
    header = FRAME_HEADER.pack(FRAME, VERSION, episode, tick, frame.width, frame.height,
                               hz, monitor_hz, status, accepted, late, rejected,
                               overrun_ticks, jump_requested, jump_applied,
                               event_sequence, last_event)
    return header + zlib.compress(frame.pixels, level=1)


def decode_frame(payload):
    if len(payload) < FRAME_HEADER.size:
        raise ValueError('Truncated frame')
    values = FRAME_HEADER.unpack_from(payload)
    if values[:2] != (FRAME, VERSION):
        raise ValueError('Unsupported frame protocol')
    names = ('type', 'version', 'episode', 'tick', 'width', 'height', 'physics_hz',
             'monitor_hz', 'status', 'accepted', 'late', 'rejected', 'overrun_ticks',
             'jump_requested', 'jump_applied', 'event_sequence', 'last_event')
    result: dict = dict(zip(names, values))
    width, height = result['width'], result['height']
    if not 64 <= width <= 4096 or not 64 <= height <= 4096:
        raise ValueError('Invalid frame dimensions')
    decoder = zlib.decompressobj()
    try:
        pixels = decoder.decompress(payload[FRAME_HEADER.size:], width * height + 1)
    except zlib.error as error:
        raise ValueError('Invalid compressed pixels') from error
    if len(pixels) != width * height or not decoder.eof or decoder.unused_data:
        raise ValueError('Invalid pixel payload')
    result['pixels'] = pixels
    return result
