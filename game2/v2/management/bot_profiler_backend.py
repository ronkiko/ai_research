"""Management service consumed by the future fullscreen Bot Profiler GUI."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from game2.v2.contracts.bot_profile import BotProfile, ModelTopology, validate_bot_id
from .bot_profiles import BotProfileStore, DEFAULT_BOT_PROFILE_DIR
from .bot_runtime import BotRuntimeLayout, DEFAULT_BOT_RUNTIME_ROOT


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BOT_CATALOG = ROOT / "game2" / "v2" / "bots" / "catalog.json"
_EDITABLE_COMPONENT_FIELDS = {
    "enabled", "implementation", "configuration", "precision", "seed", "topology",
}


def _topology_label(topology: ModelTopology) -> str:
    if topology.inputs is None or topology.outputs is None:
        return topology.kind.upper()
    values = [topology.inputs, *topology.hidden, topology.outputs]
    return "-".join(str(value) for value in values)


class BotProfilerBackend:
    """Profile CRUD and read models; contains no graphical toolkit or PyTorch."""

    def __init__(
        self,
        *,
        profile_root: str | Path = DEFAULT_BOT_PROFILE_DIR,
        runtime_root: str | Path = DEFAULT_BOT_RUNTIME_ROOT,
        catalog_path: str | Path = DEFAULT_BOT_CATALOG,
    ):
        self.profiles = BotProfileStore(profile_root)
        self.runtime_root = Path(runtime_root)
        self.catalog_path = Path(catalog_path)

    def catalog(self) -> dict[str, Any]:
        data = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        if (
            not isinstance(data, dict)
            or set(data) != {"schema_version", "roles"}
            or data.get("schema_version") != 1
            or not isinstance(data.get("roles"), dict)
        ):
            raise ValueError("Bot Profiler component catalog is invalid")
        return deepcopy(data)

    def list_bots(self) -> tuple[dict[str, Any], ...]:
        return tuple({
            "bot_id": profile.bot_id,
            "player_id": profile.player_id,
            "display_name": profile.display_name,
        } for profile in (
            self.profiles.load(bot_id) for bot_id in self.profiles.list_ids()
        ))

    def get_profile(self, bot_id: str) -> dict[str, Any]:
        return self.profiles.load(bot_id).to_dict()

    def create_bot(
        self,
        bot_id: str,
        display_name: str,
        *,
        template_id: str = "player1",
    ) -> dict[str, Any]:
        bot_id = validate_bot_id(bot_id)
        if bot_id in self.profiles.list_ids():
            raise ValueError(f"Bot Profile already exists: {bot_id}")
        template = self.profiles.load(template_id).to_dict()
        template["bot_id"] = bot_id
        template["display_name"] = display_name
        profile = BotProfile.from_dict(template)
        self.profiles.save(profile)
        return profile.to_dict()

    def save_profile(self, data: dict[str, Any]) -> dict[str, Any]:
        profile = BotProfile.from_dict(data)
        self.profiles.save(profile)
        return profile.to_dict()

    @staticmethod
    def _component_dict(profile: BotProfile, component_ref: str) -> dict[str, Any]:
        if component_ref == "cerebral_cortex":
            return profile.cerebral_cortex.to_dict()
        if component_ref == "spinal_cord":
            return profile.spinal_cord.to_dict()
        if component_ref.startswith("motor:"):
            motor_id = component_ref.split(":", 1)[1]
            for motor in profile.motors:
                if motor.motor_id == motor_id:
                    return motor.to_dict()
            raise ValueError(f"unknown Motor: {motor_id}")
        raise ValueError(f"unknown component reference: {component_ref}")

    def get_component(self, bot_id: str, component_ref: str) -> dict[str, Any]:
        profile = self.profiles.load(bot_id)
        return deepcopy(self._component_dict(profile, component_ref))

    def update_component(
        self,
        bot_id: str,
        component_ref: str,
        changes: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(changes, dict) or not changes:
            raise ValueError("component changes must be a non-empty object")
        unknown = set(changes) - _EDITABLE_COMPONENT_FIELDS
        if unknown:
            raise ValueError(
                "component fields are not editable: " + ", ".join(sorted(unknown))
            )
        profile = self.profiles.load(bot_id)
        data = profile.to_dict()

        if component_ref in {"cerebral_cortex", "spinal_cord"}:
            target = data[component_ref]
        elif component_ref.startswith("motor:"):
            motor_id = component_ref.split(":", 1)[1]
            target = next(
                (motor for motor in data["motors"] if motor["motor_id"] == motor_id),
                None,
            )
            if target is None:
                raise ValueError(f"unknown Motor: {motor_id}")
        else:
            raise ValueError(f"unknown component reference: {component_ref}")

        target.update(deepcopy(changes))
        updated = BotProfile.from_dict(data)
        self.profiles.save(updated)
        return self._component_dict(updated, component_ref)

    def describe_bot(self, bot_id: str) -> dict[str, Any]:
        profile = self.profiles.load(bot_id)

        def descriptor(ref: str, label: str, anchor: str, component) -> dict[str, Any]:
            topology = component.topology
            return {
                "component_ref": ref,
                "label": label,
                "anatomy_anchor": anchor,
                "role": component.role,
                "enabled": component.enabled,
                "implementation": component.implementation,
                "configuration": component.configuration,
                "precision": component.precision,
                "seed": component.seed,
                "topology": topology.to_dict(),
                "topology_label": _topology_label(topology),
            }

        components = [
            descriptor(
                "cerebral_cortex", "Research Strategist / LLM", "brain",
                profile.cerebral_cortex,
            ),
            descriptor(
                "spinal_cord", "Planner / CNN", "spinal_cord",
                profile.spinal_cord,
            ),
        ]
        components.extend(
            descriptor(
                f"motor:{motor.motor_id}",
                f"{motor.motor_id} Motor",
                f"motor:{motor.motor_id}",
                motor.component,
            )
            for motor in profile.motors
        )
        return {
            "bot_id": profile.bot_id,
            "player_id": profile.player_id,
            "display_name": profile.display_name,
            "components": components,
        }

    def runtime_state(
        self, bot_id: str, training_set_level: int = 1
    ) -> dict[str, object]:
        self.profiles.load(bot_id)
        return BotRuntimeLayout.resolve(
            bot_id,
            training_set_level,
            root=self.runtime_root,
        ).state()


__all__ = [
    "BotProfilerBackend",
    "DEFAULT_BOT_CATALOG",
]
