"""Session configuration and typed V2 capability manifests."""
from __future__ import annotations

import json
import socket
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from ..contracts.manifests import Endpoint


COMPATIBILITY_PLAYER_ID = "compatibility-player"
COMPATIBILITY_ACTOR_ID = "compatibility-actor"


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
    controller: str = "default"
    enable_display: bool = False
    display_mode: str = "vision"
    enable_state: bool = True
    enable_telemetry: bool = True
    enable_events: bool = True
    seed: int | None = None
    episode_limit: int | None = None
    world_ticks: int | None = 1000

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionConfig":
        if not isinstance(data, dict):
            raise ValueError("Session config must be a JSON object")
        allowed = {
            "map", "clock_mode", "physics_hz", "controller", "enable_display",
            "display_mode",
            "enable_state", "enable_telemetry", "enable_events", "seed",
            "episode_limit", "world_ticks",
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
        display_mode = values.get("display_mode", "vision")
        if not isinstance(display_mode, str) or display_mode not in {"vision", "screen"}:
            raise ValueError("display_mode must be vision or screen")
        if not isinstance(values.get("physics_hz", 120), int) or values.get("physics_hz", 120) <= 0:
            raise ValueError("physics_hz must be a positive integer")
        if not isinstance(values.get("controller", "default"), str) or not values.get("controller", "default"):
            raise ValueError("controller must be a non-empty string")
        for name in ("enable_display", "enable_state", "enable_telemetry", "enable_events"):
            if type(values.get(name, getattr(cls, name))) is not bool:
                raise ValueError(f"{name} must be boolean")
        if values.get("seed") is not None and type(values["seed"]) is not int:
            raise ValueError("seed must be an integer or null")
        for name in ("episode_limit", "world_ticks"):
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
class InternalManifest:
    """Private console wiring. This object is never given to a Player."""

    session_id: str
    engine_control: Endpoint
    engine_state: Endpoint | None
    engine_telemetry: Endpoint | None
    engine_events: Endpoint | None
    run_dir: str

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        for name in ("engine_control", "engine_state", "engine_telemetry", "engine_events"):
            endpoint = getattr(self, name)
            result[name] = endpoint.as_dict() if endpoint else None
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InternalManifest":
        if not isinstance(data, dict):
            raise ValueError("Internal manifest must be a JSON object")
        required = {"session_id", "engine_control", "engine_state",
                    "engine_telemetry", "engine_events", "run_dir"}
        if set(data) != required:
            raise ValueError("Internal manifest fields are invalid")

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
        control = endpoint(data["engine_control"])
        if control is None:
            raise ValueError("engine_control endpoint is required")
        return cls(data["session_id"], control, endpoint(data["engine_state"]),
                   endpoint(data["engine_telemetry"]), endpoint(data["engine_events"]), data["run_dir"])

    @classmethod
    def from_file(cls, path: str | Path) -> "InternalManifest":
        with Path(path).open(encoding="utf-8") as source:
            return cls.from_dict(_strict_json(source.read()))

    def write(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), sort_keys=True), encoding="utf-8")


def _manifest_endpoint(value: Any, required: bool = True) -> Endpoint | None:
    if value is None:
        if required:
            raise ValueError("required endpoint is missing")
        return None
    if not isinstance(value, dict) or set(value) != {"host", "port"}:
        raise ValueError("Invalid endpoint")
    return Endpoint(value["host"], value["port"])


def _manifest_session(data: dict[str, Any], fields: set[str]) -> str:
    if not isinstance(data, dict) or set(data) != fields:
        raise ValueError("Manifest fields are invalid")
    session_id = data.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("session_id must be non-empty")
    return session_id


@dataclass(frozen=True)
class EngineManifest:
    """Only the endpoints and run directory required by Engine."""

    session_id: str
    control: Endpoint
    state: Endpoint | None
    telemetry: Endpoint | None
    events: Endpoint | None
    run_dir: str
    player_id: str | None = None
    actor_id: str | None = None

    def __post_init__(self) -> None:
        if (self.player_id is None) != (self.actor_id is None):
            raise ValueError("Engine compatibility binding requires player_id and actor_id")
        for name in ("player_id", "actor_id"):
            value = getattr(self, name)
            if value is not None and (type(value) is not str or not value):
                raise ValueError(f"{name} must be a non-empty string or null")

    def to_dict(self) -> dict[str, Any]:
        return {"session_id": self.session_id, "control": self.control.as_dict(),
                "state": self.state.as_dict() if self.state else None,
                "telemetry": self.telemetry.as_dict() if self.telemetry else None,
                "events": self.events.as_dict() if self.events else None,
                "run_dir": self.run_dir, "player_id": self.player_id,
                "actor_id": self.actor_id}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EngineManifest":
        session_id = _manifest_session(data, {"session_id", "control", "state",
                                               "telemetry", "events", "run_dir",
                                               "player_id", "actor_id"})
        run_dir = data["run_dir"]
        if not isinstance(run_dir, str) or not run_dir:
            raise ValueError("run_dir must be non-empty")
        return cls(session_id, cast(Endpoint, _manifest_endpoint(data["control"])),
                   _manifest_endpoint(data["state"], False),
                   _manifest_endpoint(data["telemetry"], False),
                   _manifest_endpoint(data["events"], False), run_dir,
                   data["player_id"], data["actor_id"])

    @classmethod
    def from_file(cls, path: str | Path) -> "EngineManifest":
        with Path(path).open(encoding="utf-8") as source:
            return cls.from_dict(_strict_json(source.read()))

    def write(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), sort_keys=True), encoding="utf-8")


@dataclass(frozen=True)
class ControllerManifest:
    """Controller capabilities: public Joystick ingress and private Engine CONTROL."""

    session_id: str
    engine_control: Endpoint
    joystick: Endpoint
    actor_id: str = COMPATIBILITY_ACTOR_ID

    def __post_init__(self) -> None:
        if type(self.actor_id) is not str or not self.actor_id:
            raise ValueError("actor_id must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "engine_control": self.engine_control.as_dict(),
            "joystick": self.joystick.as_dict(),
            "actor_id": self.actor_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ControllerManifest":
        session_id = _manifest_session(
            data, {"session_id", "engine_control", "joystick", "actor_id"}
        )
        return cls(
            session_id,
            cast(Endpoint, _manifest_endpoint(data["engine_control"])),
            cast(Endpoint, _manifest_endpoint(data["joystick"])),
            data["actor_id"],
        )

    @classmethod
    def from_file(cls, path: str | Path) -> "ControllerManifest":
        with Path(path).open(encoding="utf-8") as source:
            return cls.from_dict(_strict_json(source.read()))

    def write(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), sort_keys=True), encoding="utf-8")


@dataclass(frozen=True)
class DisplayManifest:
    """Private Display capability: STATE, static World, and presentation mode."""

    session_id: str
    engine_state: Endpoint
    world_file: str
    mode: str
    vision: Endpoint | None = None
    self_actor_id: str = COMPATIBILITY_ACTOR_ID

    def __post_init__(self) -> None:
        if not isinstance(self.world_file, str) or not self.world_file:
            raise ValueError("world_file must be a non-empty string")
        if not isinstance(self.mode, str) or self.mode not in {"vision", "screen"}:
            raise ValueError("mode must be vision or screen")
        if self.mode == "screen" and self.vision is not None:
            raise ValueError("screen Display cannot bind a Vision endpoint")
        if type(self.self_actor_id) is not str or not self.self_actor_id:
            raise ValueError("self_actor_id must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        return {"session_id": self.session_id,
                "engine_state": self.engine_state.as_dict(),
                "world_file": self.world_file, "mode": self.mode,
                "vision": self.vision.as_dict() if self.vision else None,
                "self_actor_id": self.self_actor_id}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DisplayManifest":
        session_id = _manifest_session(data, {"session_id", "engine_state",
                                               "world_file", "mode", "vision",
                                               "self_actor_id"})
        return cls(session_id, cast(Endpoint, _manifest_endpoint(data["engine_state"])),
                   data["world_file"], data["mode"],
                   _manifest_endpoint(data["vision"], False), data["self_actor_id"])

    @classmethod
    def from_file(cls, path: str | Path) -> "DisplayManifest":
        with Path(path).open(encoding="utf-8") as source:
            return cls.from_dict(_strict_json(source.read()))

    def write(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), sort_keys=True), encoding="utf-8")



@dataclass(frozen=True)
class ScreenSourceManifest:
    """Private Console wiring for one headless spectator Screen source."""

    session_id: str
    engine_state: Endpoint
    world_file: str
    screen: Endpoint
    physics_hz: int
    episode_limit: int | None
    view: str = "screen"

    def __post_init__(self) -> None:
        if type(self.session_id) is not str or not self.session_id:
            raise ValueError("session_id must be non-empty")
        if type(self.world_file) is not str or not self.world_file:
            raise ValueError("world_file must be non-empty")
        if type(self.physics_hz) is not int or self.physics_hz <= 0:
            raise ValueError("physics_hz must be a positive integer")
        if self.view not in {"screen", "vision"}:
            raise ValueError("Screen source view must be screen or vision")
        if self.episode_limit is not None and (
            type(self.episode_limit) is not int or self.episode_limit <= 0
        ):
            raise ValueError("episode_limit must be a positive integer or null")

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "engine_state": self.engine_state.as_dict(),
            "world_file": self.world_file,
            "screen": self.screen.as_dict(),
            "physics_hz": self.physics_hz,
            "episode_limit": self.episode_limit,
            "view": self.view,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScreenSourceManifest":
        session_id = _manifest_session(
            data, {
                "session_id", "engine_state", "world_file", "screen",
                "physics_hz", "episode_limit", "view",
            }
        )
        return cls(
            session_id,
            cast(Endpoint, _manifest_endpoint(data["engine_state"])),
            data["world_file"],
            cast(Endpoint, _manifest_endpoint(data["screen"])),
            data["physics_hz"],
            data["episode_limit"],
            data["view"],
        )

    @classmethod
    def from_file(cls, path: str | Path) -> "ScreenSourceManifest":
        with Path(path).open(encoding="utf-8") as source:
            return cls.from_dict(_strict_json(source.read()))

    def write(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), sort_keys=True), encoding="utf-8")


@dataclass(frozen=True)
class OperatorControlManifest:
    """Private lifecycle capability for a temporary operator shell."""

    session_id: str
    control: Endpoint
    actor_id: str = COMPATIBILITY_ACTOR_ID

    def __post_init__(self) -> None:
        if type(self.actor_id) is not str or not self.actor_id:
            raise ValueError("actor_id must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        return {"session_id": self.session_id, "control": self.control.as_dict(),
                "actor_id": self.actor_id}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OperatorControlManifest":
        session_id = _manifest_session(data, {"session_id", "control", "actor_id"})
        return cls(session_id, cast(Endpoint, _manifest_endpoint(data["control"])),
                   data["actor_id"])

    @classmethod
    def from_file(cls, path: str | Path) -> "OperatorControlManifest":
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


__all__ = ["COMPATIBILITY_ACTOR_ID", "COMPATIBILITY_PLAYER_ID", "ControllerManifest", "DisplayManifest", "EngineManifest",
           "InternalManifest", "OperatorControlManifest", "ScreenSourceManifest", "SessionConfig",
           "allocate_endpoint", "new_session_id"]
