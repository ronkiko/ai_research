"""Persistent Bot Profile storage owned by the Management plane."""
from __future__ import annotations

import os
from pathlib import Path

from game2.v2.contracts.bot_profile import BotProfile, validate_bot_id


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BOT_PROFILE_DIR = ROOT / "game2" / "v2" / "bots"


class BotProfileStore:
    """Strict file-backed source of truth for editable Bot Profiles."""

    def __init__(self, root: str | Path = DEFAULT_BOT_PROFILE_DIR):
        self.root = Path(root)

    def list_ids(self) -> tuple[str, ...]:
        if not self.root.exists():
            return ()
        result = []
        for path in sorted(self.root.glob("*.json")):
            if path.name == "catalog.json":
                continue
            if not path.is_file():
                continue
            profile = BotProfile.from_file(path)
            if path.stem != profile.bot_id:
                raise ValueError(
                    f"Bot Profile filename {path.name} does not match bot_id "
                    f"{profile.bot_id}"
                )
            result.append(profile.bot_id)
        return tuple(result)

    def path_for(self, bot_id: str) -> Path:
        bot_id = validate_bot_id(bot_id)
        if bot_id == "catalog":
            raise ValueError("catalog is reserved for the component catalog")
        return self.root / f"{bot_id}.json"

    def load(self, bot_id: str) -> BotProfile:
        path = self.path_for(bot_id)
        try:
            profile = BotProfile.from_file(path)
        except FileNotFoundError as exc:
            raise ValueError(f"unknown Bot Profile: {bot_id}") from exc
        if profile.bot_id != bot_id:
            raise ValueError("Bot Profile identity does not match requested bot_id")
        return profile

    def save(self, profile: BotProfile) -> Path:
        if not isinstance(profile, BotProfile):
            raise TypeError("save requires a BotProfile")
        self.root.mkdir(parents=True, exist_ok=True)
        destination = self.path_for(profile.bot_id)
        temporary = destination.with_name(destination.name + ".tmp")
        temporary.write_text(profile.to_json(), encoding="utf-8")
        try:
            # Re-read before publish so malformed serialization can never replace
            # the current profile.
            verified = BotProfile.from_file(temporary)
            if verified != profile:
                raise ValueError("Bot Profile serialization did not round-trip")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination


__all__ = ["BotProfileStore", "DEFAULT_BOT_PROFILE_DIR"]
