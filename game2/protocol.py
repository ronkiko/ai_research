"""Version 3 binary socket protocol; integers and floats use network byte order."""
import math
import struct
import zlib
from dataclasses import dataclass

VERSION = 3
ACTION, RESET, FRAME, FEATURES = 1, 2, 128, 129
PREFIX = struct.Struct('!I')
ACTION_PACKET = struct.Struct('!BIIQHBB')
RESET_PACKET = struct.Struct('!BII')
# type, version, episode, tick, w, h, physics_hz, monitor_hz, status,
# last accepted action/reset sequence, late, rejected, overrun_ticks,
# jump_requested, jump_applied, event_sequence, last_event
# (0 none, 1 die, 2 success, 3 reset); normalized velocity_x follows.
FRAME_HEADER = struct.Struct('!BBIQHHHHBIIIIIIIBf')
# type, version, episode, tick, status, accepted, late, rejected,
# overrun_ticks, jump_requested, jump_applied, event_sequence, last_event,
# distance_to_gap, grounded, normalized velocity_x
FEATURES_PACKET = struct.Struct('!BBIQBIIIIIIIBfBf')
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
                 event_sequence, last_event, velocity_x):
    if (isinstance(velocity_x, bool) or not isinstance(velocity_x, (int, float))
            or not math.isfinite(velocity_x) or not -1.0 <= velocity_x <= 1.0):
        raise ValueError('velocity_x must be finite and in [-1, 1]')
    header = FRAME_HEADER.pack(FRAME, VERSION, episode, tick, frame.width, frame.height,
                               hz, monitor_hz, status, accepted, late, rejected,
                               overrun_ticks, jump_requested, jump_applied,
                               event_sequence, last_event, velocity_x)
    return header + zlib.compress(frame.pixels, level=1)


def decode_frame(payload):
    if len(payload) < FRAME_HEADER.size:
        raise ValueError('Truncated frame')
    values = FRAME_HEADER.unpack_from(payload)
    if values[:2] != (FRAME, VERSION):
        raise ValueError('Unsupported frame protocol')
    names = ('type', 'version', 'episode', 'tick', 'width', 'height', 'physics_hz',
             'monitor_hz', 'status', 'accepted', 'late', 'rejected', 'overrun_ticks',
             'jump_requested', 'jump_applied', 'event_sequence', 'last_event',
             'velocity_x')
    result: dict = dict(zip(names, values))
    width, height = result['width'], result['height']
    if not 64 <= width <= 4096 or not 64 <= height <= 4096:
        raise ValueError('Invalid frame dimensions')
    if not math.isfinite(result['velocity_x']) or not -1.0 <= result['velocity_x'] <= 1.0:
        raise ValueError('Invalid velocity_x')
    decoder = zlib.decompressobj()
    try:
        pixels = decoder.decompress(payload[FRAME_HEADER.size:], width * height + 1)
    except zlib.error as error:
        raise ValueError('Invalid compressed pixels') from error
    if len(pixels) != width * height or not decoder.eof or decoder.unused_data:
        raise ValueError('Invalid pixel payload')
    result['pixels'] = pixels
    return result


def encode_features(*, episode, tick, status, accepted, late, rejected,
                     overrun_ticks, jump_requested, jump_applied, event_sequence,
                     last_event, distance_to_gap, grounded, velocity_x):
    if (isinstance(distance_to_gap, bool) or not isinstance(distance_to_gap, (int, float))
            or not math.isfinite(distance_to_gap) or not -1.0 <= distance_to_gap <= 1.0):
        raise ValueError('distance_to_gap must be finite and in [-1, 1]')
    if type(grounded) is not bool:
        raise ValueError('grounded must be boolean')
    if (isinstance(velocity_x, bool) or not isinstance(velocity_x, (int, float))
            or not math.isfinite(velocity_x) or not -1.0 <= velocity_x <= 1.0):
        raise ValueError('velocity_x must be finite and in [-1, 1]')
    return FEATURES_PACKET.pack(FEATURES, VERSION, episode, tick, status, accepted,
                                 late, rejected, overrun_ticks, jump_requested,
                                 jump_applied, event_sequence, last_event,
                                 distance_to_gap, grounded, velocity_x)


def decode_features(payload):
    if len(payload) != FEATURES_PACKET.size:
        raise ValueError('Invalid compact observation length')
    values = FEATURES_PACKET.unpack(payload)
    if values[:2] != (FEATURES, VERSION):
        raise ValueError('Unsupported compact observation protocol')
    names = ('type', 'version', 'episode', 'tick', 'status', 'accepted', 'late',
             'rejected', 'overrun_ticks', 'jump_requested', 'jump_applied',
             'event_sequence', 'last_event', 'distance_to_gap', 'grounded',
             'velocity_x')
    result: dict = dict(zip(names, values))
    if result['status'] not in (0, 1, 2) or result['last_event'] not in (0, 1, 2, 3):
        raise ValueError('Invalid compact observation status')
    if result['grounded'] not in (0, 1):
        raise ValueError('Invalid compact observation grounded flag')
    if not math.isfinite(result['distance_to_gap']) or not -1.0 <= result['distance_to_gap'] <= 1.0:
        raise ValueError('Invalid compact observation distance')
    if not math.isfinite(result['velocity_x']) or not -1.0 <= result['velocity_x'] <= 1.0:
        raise ValueError('Invalid compact observation velocity')
    result['grounded'] = bool(result['grounded'])
    result['features'] = (result['distance_to_gap'],
                          1.0 if result['grounded'] else 0.0,
                          result['velocity_x'])
    return result
