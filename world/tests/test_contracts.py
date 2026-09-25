from __future__ import annotations

import copy
import json
import math
import unittest

from world.contracts import (
    ActionAuthority, ActionLedger, ActionReceipt, ActionRequest, ActionTarget,
    BodyProfileRef, ContractError, ControllerBinding, DialogueMessage,
    EmbodimentBinding, IdentityRegistry, PhysicalState, SCHEMA_VERSION,
    SkillBinding, TutorialFlowState, WorldObservation, validate_observation,
)

H = "a" * 64
P = "0e6f1b013f39814574a88844ccc7bb10b41fb2e21d797920378a164a984029df"


def binding():
    return EmbodimentBinding(
        SCHEMA_VERSION,
        "character.yuki",
        "embodiment.yuki.primary",
        "entity.yuki",
        "yuki-world-v1",
        BodyProfileRef("point_x_v1", H),
        ControllerBinding("controller.yuki.body", 4),
    )


def authority(kind="navigate", target="laboratory"):
    return ActionAuthority(
        SCHEMA_VERSION, "authority.1", "character.yuki", kind, target, 2_000_000_000_000
    )


class ContractTests(unittest.TestCase):
    def test_binding_roundtrip_preserves_identity_and_has_no_session_identity(self):
        original = binding()
        decoded = EmbodimentBinding.from_json(original.to_json())
        self.assertEqual(decoded, original)
        self.assertNotIn("session", original.to_dict())
        registry = IdentityRegistry()
        self.assertIs(registry.register(original), original)
        self.assertEqual(registry.register(decoded), original)

    def test_identity_registry_rejects_entity_substitution(self):
        registry = IdentityRegistry()
        registry.register(binding())
        forged = EmbodimentBinding(
            SCHEMA_VERSION, "character.yuki", "embodiment.yuki.primary", "entity.other",
            "yuki-world-v1", BodyProfileRef("point_x_v1", H),
            ControllerBinding("controller.yuki.body", 4),
        )
        with self.assertRaises(ContractError) as caught:
            registry.register(forged)
        self.assertEqual(caught.exception.code, "identity_mismatch")

    def test_observation_roundtrip_and_source_validation(self):
        obs = WorldObservation(
            SCHEMA_VERSION, "obs.1", "yuki-world-v1", "epoch.1", 120, 7,
            "entity.yuki", "hallway", PhysicalState(12.5, -3.0, 0.25), H, P,
        )
        decoded = WorldObservation.from_json(obs.to_json())
        self.assertEqual(decoded, obs)
        validate_observation(binding(), decoded, physics_contract_hash=P)

        forged = copy.deepcopy(decoded.to_dict())
        forged["entity_id"] = "entity.other"
        with self.assertRaises(ContractError) as caught:
            validate_observation(binding(), WorldObservation.from_dict(forged),
                                 physics_contract_hash=P)
        self.assertEqual(caught.exception.code, "identity_mismatch")

    def test_non_finite_physical_values_are_rejected(self):
        for bad in (math.nan, math.inf, -math.inf):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                PhysicalState(bad, 0.0, 0.0)
        receipt = {
            "schema_version": 1, "action_id": "action.1", "request_id": "request.1",
            "status": "applied", "reason_code": "ok", "source_zone": "hallway",
            "target_zone": "laboratory", "entity_id": "entity.yuki",
            "world_epoch": "epoch.1", "tick": 1, "world_revision": 1,
            "observed_outcome": {"x": math.inf},
        }
        with self.assertRaises(ValueError):
            ActionReceipt.from_dict(receipt)

    def test_unknown_contract_version_is_rejected(self):
        data = binding().to_dict()
        data["schema_version"] = 99
        with self.assertRaises(ContractError) as caught:
            EmbodimentBinding.from_dict(data)
        self.assertEqual(caught.exception.code, "unsupported_version")

    def test_action_request_hash_scope_and_idempotency(self):
        request = ActionRequest.create(
            request_id="request.1",
            embodiment_id="embodiment.yuki.primary",
            kind="navigate",
            target=ActionTarget("location", "laboratory"),
            expected_world_epoch="epoch.1",
            expected_world_revision=8,
            authority=authority(),
        )
        self.assertEqual(ActionRequest.from_json(request.to_json()), request)

        ledger = ActionLedger()
        self.assertEqual(ledger.reserve(request, "action.1"), "action.1")
        self.assertEqual(ledger.reserve(request, "action.2"), "action.1")

        changed = request.to_dict()
        changed["target"]["id"] = "training/flat_run"
        changed["authority"]["target_scope"] = "training/flat_run"
        changed["content_hash"] = ActionRequest.create(
            request_id="request.1",
            embodiment_id="embodiment.yuki.primary",
            kind="navigate",
            target=ActionTarget("location", "training/flat_run"),
            expected_world_epoch="epoch.1",
            expected_world_revision=8,
            authority=authority(target="training/flat_run"),
        ).content_hash
        forged = ActionRequest.from_dict(changed)
        with self.assertRaises(ContractError) as caught:
            ledger.reserve(forged, "action.3")
        self.assertEqual(caught.exception.code, "request_conflict")

    def test_action_request_rejects_tampering_and_wrong_authority(self):
        request = ActionRequest.create(
            request_id="request.2", embodiment_id="embodiment.yuki.primary",
            kind="approach", target=ActionTarget("object", "workstation"),
            expected_world_epoch="epoch.1", expected_world_revision=1,
            authority=authority("approach", "workstation"),
        )
        tampered = request.to_dict()
        tampered["expected_world_revision"] = 2
        with self.assertRaises(ContractError) as caught:
            ActionRequest.from_dict(tampered)
        self.assertEqual(caught.exception.code, "request_conflict")

        with self.assertRaises(ContractError) as caught:
            ActionRequest.create(
                request_id="request.3", embodiment_id="embodiment.yuki.primary",
                kind="navigate", target=ActionTarget("location", "laboratory"),
                expected_world_epoch="epoch.1", expected_world_revision=1,
                authority=authority("approach", "laboratory"),
            )
        self.assertEqual(caught.exception.code, "capability_denied")

    def test_skill_binding_is_path_free_and_roundtrips(self):
        skill = SkillBinding(
            SCHEMA_VERSION, "skill.walk.flat.v1", "embodiment.yuki.primary",
            "motor.uuid.1", "motor.certificate.1", H, "spine.checkpoint.1", H,
            H, H, H, P,
        )
        decoded = SkillBinding.from_json(skill.to_json())
        self.assertEqual(decoded, skill)
        raw = json.dumps(decoded.to_dict())
        self.assertNotIn("/tmp/", raw)
        self.assertNotIn("path", raw)

    def test_dialogue_and_tutorial_contracts_are_separate_from_world_revision(self):
        message = DialogueMessage(SCHEMA_VERSION, "message.1", "director", 1, "Привет")
        self.assertEqual(DialogueMessage.from_dict(message.to_dict()), message)
        flow = TutorialFlowState(
            SCHEMA_VERSION, "flow.first_day", "intro_dialogue", None, 42.5,
            "vn_dialogue", "locked",
        )
        self.assertEqual(TutorialFlowState.from_dict(flow.to_dict()), flow)
        self.assertNotIn("world_revision", flow.to_dict())


if __name__ == "__main__":
    unittest.main()
