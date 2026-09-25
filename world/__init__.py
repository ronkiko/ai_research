"""Embodied world contracts and map catalog (additive refactor stage 02)."""

from .catalog import MAP_FILES, WORLD_ID, MapCatalog, MapManifest
from .contracts import (
    ActionAuthority,
    ActionLedger,
    ActionReceipt,
    ActionRequest,
    ActionTarget,
    BodyProfileRef,
    ContractError,
    ControllerBinding,
    DialogueMessage,
    EmbodimentBinding,
    IdentityRegistry,
    PhysicalState,
    SCHEMA_VERSION,
    SkillBinding,
    TutorialFlowState,
    WorldObservation,
    canonical_hash,
    canonical_json,
    validate_observation,
)

__all__ = [
    "ActionAuthority", "ActionLedger", "ActionReceipt", "ActionRequest", "ActionTarget",
    "BodyProfileRef", "ContractError", "ControllerBinding", "DialogueMessage",
    "EmbodimentBinding", "IdentityRegistry", "MAP_FILES", "MapCatalog", "MapManifest",
    "PhysicalState", "SCHEMA_VERSION", "SkillBinding", "TutorialFlowState", "WORLD_ID",
    "WorldObservation", "canonical_hash", "canonical_json", "validate_observation",
]
