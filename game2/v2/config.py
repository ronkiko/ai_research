"""Session configuration and the immutable runtime topology description."""
from __future__ import annotations

import json
import socket
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


def _strict_json(text: str) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate field: {key}")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError(f"Invalid JSON number: {value}")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)


@dataclass(frozen=True)
class SessionConfig:
    map: str
    clock_mode: str = "unpaced"
    physics_hz: int = 120
    controller: str = "scripted"
    enable_ui: bool = False
    enable_state: bool = True
    enable_telemetry: bool = True
    enable_events: bool = True
    seed: int | None = None
    episode_limit: int | None = None
    session_ticks: int = 1000

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionConfig":
        if not isinstance(data, dict):
            raise ValueError("Session config must be a JSON object")
        allowed = {
            "map", "clock_mode", "physics_hz", "controller", "enable_ui",
            "enable_state", "enable_telemetry", "enable_events", "seed",
            "episode_limit", "session_ticks",
        }
        missing = {"map"} - data.keys()
        extra = data.keys() - allowed
        if missing or extra:
            raise ValueError(f"Missing fields: {sorted(missing)}; unknown fields: {sorted(extra)}")
        values = dict(data)
        if not isinstance(values["map"], str) or not values["map"]:
            raise ValueError("map must be a non-empty string")
        if values.get("clock_mode", "unpaced") not in {"realtime", "unpaced"}:
            raise ValueError("clock_mode must be realtime or unpaced")
        if not isinstance(values.get("physics_hz", 120), int) or values.get("physics_hz", 120) <= 0:
            raise ValueError("physics_hz must be a positive integer")
        if not isinstance(values.get("controller", "scripted"), str) or not values.get("controller", "scripted"):
            raise ValueError("controller must be a non-empty string")
        for name in ("enable_ui", "enable_state", "enable_telemetry", "enable_events"):
            if type(values.get(name, getattr(cls, name))) is not bool:
                raise ValueError(f"{name} must be boolean")
        if values.get("seed") is not None and type(values["seed"]) is not int:
            raise ValueError("seed must be an integer or null")
        for name in ("episode_limit", "session_ticks"):
            value = values.get(name, getattr(cls, name))
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError(f"{name} must be a positive integer or null")
        return cls(**values)

    @classmethod
    def from_file(cls, path: str | Path) -> "SessionConfig":
        path = Path(path)
        with path.open(encoding="utf-8") as source:
            text = source.read(1_000_001)
        if len(text) > 1_000_000:
            raise ValueError("Session config exceeds 1 MB")
        return cls.from_dict(_strict_json(text))

    def map_path(self, config_path: str | Path) -> Path:
        path = Path(self.map)
        return path if path.is_absolute() else Path(config_path).resolve().parent / path


@dataclass(frozen=True)
class Endpoint:
    host: str
    port: int

    def __post_init__(self):
        if not isinstance(self.host, str) or not self.host:
            raise ValueError("endpoint host must be non-empty")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("endpoint port must be in 1..65535")

    def as_dict(self) -> dict[str, Any]:
        return {"host": self.host, "port": self.port}


@dataclass(frozen=True)
class RuntimeManifest:
    session_id: str
    control: Endpoint
    state: Endpoint | None
    telemetry: Endpoint | None
    events: Endpoint | None
    run_dir: str

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        for name in ("control", "state", "telemetry", "events"):
            endpoint = getattr(self, name)
            result[name] = endpoint.as_dict() if endpoint else None
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuntimeManifest":
        if not isinstance(data, dict):
            raise ValueError("Runtime manifest must be a JSON object")
        required = {"session_id", "control", "state", "telemetry", "events", "run_dir"}
        if set(data) != required:
            raise ValueError("Runtime manifest fields are invalid")

        def endpoint(value):
            if value is None:
                return None
            if not isinstance(value, dict) or set(value) != {"host", "port"}:
                raise ValueError("Invalid endpoint")
            return Endpoint(value["host"], value["port"])

        if not isinstance(data["session_id"], str) or not data["session_id"]:
            raise ValueError("session_id must be non-empty")
        if not isinstance(data["run_dir"], str) or not data["run_dir"]:
            raise ValueError("run_dir must be non-empty")
        control = endpoint(data["control"])
        if control is None:
            raise ValueError("control endpoint is required")
        return cls(data["session_id"], control, endpoint(data["state"]),
                   endpoint(data["telemetry"]), endpoint(data["events"]), data["run_dir"])

    @classmethod
    def from_file(cls, path: str | Path) -> "RuntimeManifest":
        with Path(path).open(encoding="utf-8") as source:
            return cls.from_dict(_strict_json(source.read()))

    def write(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), sort_keys=True), encoding="utf-8")


def allocate_endpoint() -> Endpoint:
    """Select a currently free loopback port; children bind it after launch."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return Endpoint("127.0.0.1", probe.getsockname()[1])


def new_session_id() -> str:
    return uuid.uuid4().hex
