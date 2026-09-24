"""Network and simulation defaults for GameServer v1."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

HOST = "127.0.0.1"
GATEWAY_PORT = 17600
PERSISTENCE_PORT = 17601
WORLD_PORT = 17602
ZONE_PORT = 17603
TELEMETRY_UDP_PORT = 17604
TELEMETRY_QUERY_PORT = 17605
PHYSICS_HZ = 120
ZONE_ID = "zone1"
WORLD_ID = "world1"
TELEMETRY_RING_TICKS = PHYSICS_HZ
REST_VELOCITY_EPS = 0.05
REST_MOTOR_EPS = 0.02

@dataclass(frozen=True)
class LineConfig:
    length: float = 1000.0
    player_max_speed: float = 180.0
    player_max_acceleration: float = 720.0
    player_drag: float = 4.0
    mob_max_speed: float = 120.0
    mob_max_acceleration: float = 480.0
    mob_drag: float = 4.0

LINE = LineConfig()

PHYSICS_CONTRACT_VERSION = 1
PHYSICS_DYNAMICS_MODEL = "continuous_1d_euler_v1"
PHYSICS_CONTRACT = {
    "dynamics_model": PHYSICS_DYNAMICS_MODEL,
    "physics_hz": PHYSICS_HZ,
    "line_length": LINE.length,
    "player_max_speed": LINE.player_max_speed,
    "player_max_acceleration": LINE.player_max_acceleration,
    "player_drag": LINE.player_drag,
    "rest_velocity_eps": REST_VELOCITY_EPS,
    "rest_motor_eps": REST_MOTOR_EPS,
}
PHYSICS_CONTRACT_SHA256 = hashlib.sha256(
    json.dumps(
        PHYSICS_CONTRACT,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()
