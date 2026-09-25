"""Validated, immutable character conditioning for composite Brain agents."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


CHARACTER_CORE_VERSION = 2
DEFAULT_CHARACTER_PATH = Path(__file__).resolve().parents[1] / "characters" / "yuki-02" / "character.json"
REQUIRED_TRAITS = {
    "moe_expressiveness", "bashfulness", "tenderness",
    "attachment_intensity", "abandonment_sensitivity", "devotion",
    "jealousy_sensitivity", "possessiveness", "romantic_idealization",
    "authority_deference", "self_integrity", "reactance",
    "rejection_sensitivity", "dependency_susceptibility",
    "stress_tolerance", "personality_plasticity",
}


class CharacterCoreError(RuntimeError):
    pass


def character_path() -> Path:
    configured = os.environ.get("GAMELAB_CHARACTER_PROFILE")
    return Path(configured) if configured else DEFAULT_CHARACTER_PATH


class CharacterCore:
    """Stable model input. It describes tendencies but never selects behavior."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or character_path())
        try:
            raw = self.path.read_bytes()
            payload = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CharacterCoreError(f"cannot load character profile: {error}") from error
        if not isinstance(payload, dict) or payload.get("version") != CHARACTER_CORE_VERSION:
            raise CharacterCoreError("unsupported character profile version")
        if not isinstance(payload.get("character_id"), str) or not payload["character_id"]:
            raise CharacterCoreError("character_id is required")
        archetypes = payload.get("archetypes")
        if not isinstance(archetypes, list) or not archetypes or not all(
            isinstance(item, str) and item for item in archetypes
        ):
            raise CharacterCoreError("archetypes must be a non-empty string list")
        if payload.get("adult") is not True:
            raise CharacterCoreError("character must explicitly be adult")
        summary = payload.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise CharacterCoreError("summary is required")
        for field in ("behavioral_priors", "speech_priors", "tensions"):
            values = payload.get(field)
            if not isinstance(values, list) or not values or not all(
                isinstance(item, str) and item.strip() for item in values
            ):
                raise CharacterCoreError(f"{field} must be a non-empty string list")
        traits = payload.get("traits")
        if not isinstance(traits, dict) or set(traits) != REQUIRED_TRAITS:
            raise CharacterCoreError("character traits do not match the v2 contract")
        for name, value in traits.items():
            if type(value) is bool or not isinstance(value, (int, float)) or not 0 <= float(value) <= 100:
                raise CharacterCoreError(f"trait {name} must be numeric within [0,100]")
        self._raw = raw
        self._payload = payload
        self._hash = hashlib.sha256(raw).hexdigest()

    def public(self) -> dict[str, Any]:
        return {
            "character_core_version": CHARACTER_CORE_VERSION,
            "character_id": self._payload["character_id"],
            "name": self._payload.get("name"),
            "adult": True,
            "archetypes": list(self._payload["archetypes"]),
            "calibration": self._payload.get("calibration"),
            "summary": self._payload["summary"],
            "traits": {name: float(value) for name, value in self._payload["traits"].items()},
            "behavioral_priors": list(self._payload["behavioral_priors"]),
            "speech_priors": list(self._payload["speech_priors"]),
            "tensions": list(self._payload["tensions"]),
            "profile_sha256": self._hash,
            "interpretation": self._payload.get("interpretation"),
        }


__all__ = ["CharacterCore", "CharacterCoreError", "CHARACTER_CORE_VERSION"]
