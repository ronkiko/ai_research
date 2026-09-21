"""Network and simulation defaults for the GameServer v1 laboratory."""
from __future__ import annotations

from dataclasses import dataclass


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


@dataclass(frozen=True)
class ArenaConfig:
    width: float = 1000.0
    height: float = 600.0
    player_speed: float = 180.0
    mob_speed: float = 120.0


ARENA = ArenaConfig()
