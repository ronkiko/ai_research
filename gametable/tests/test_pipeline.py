from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import unittest

from gametable.roleplay import prompts
from gametable.roleplay.engine import (
    apply_effect_plan, build_contract, build_effect_plan, calculation_audit,
    decide_turn, load_rules,
)
from gametable.roleplay.external import ActionExecutor
from gametable.roleplay.store import Store


def event(intent_id="talk"):
    return {
        "id": "pipeline-0001",
        "text": "Проверь лабораторный стенд",
        "intent_id": intent_id,
    }


def report(role, e, state, disposition="respond"):
    return {
        "event_id": e["id"],
        "revision": state["revision"],
        "role": role,
        "category": "research",
        "impacts": {"mood": 0, "affection": 0, "trust": 0},
        "scores": {
            name: (1 if name == disposition else -1)
            for name in ("respond", "accept", "decline", "clarify")
        },
        "evidence": [e["text"]],
        "summary": "test",
    }


class NavigationStub:
    def __init__(self, *, fail=False, status="queued"):
        self.fail = fail
        self.status_value = status
        self.starts = []
        self.status_calls = []

    def navigate(self, target_id, request_id):
        self.starts.append(("navigate", target_id, request_id))
        if self.fail:
            raise RuntimeError("transport lost")
        return {
            "action_id": "nav.pipeline.1",
            "request_id": request_id,
            "status": self.status_value,
        }

    def approach(self, target_id, request_id):
        self.starts.append(("approach", target_id, request_id))
        if self.fail:
            raise RuntimeError("transport lost")
        return {
            "action_id": "nav.pipeline.2",
            "request_id": request_id,
            "status": self.status_value,
        }

    def action_status(self, action_id):
        self.status_calls.append(action_id)
        return {
            "action_id": action_id,
            "status": "arrived",
            "current_observation": {
                "world_epoch": "epoch.pipeline",
                "observed_tick": 91,
                "location_id": "laboratory",
                "physical": {"x": 1.0, "vx": 0.0, "effort": 0.0},
            },
        }

    def close(self):
        pass


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.rules = load_rules()
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "save.sqlite3", self.rules)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def explicit_lab_pipeline(self):
        before = self.store.state()
        e = event("request_lab_work")
        heart = report("heart", e, before, "accept")
        head = report("head", e, before, "accept")
        decision, context = decide_turn(before, e, heart, head, self.rules)
        effect_plan = build_effect_plan(
            before, e, decision, context, self.rules
        )
        after, character_state = apply_effect_plan(
            before, e, decision, effect_plan, context, self.rules
        )
        contract = build_contract(
            e, after, decision, effect_plan, context, self.rules
        )
        audit = calculation_audit(
            decision, effect_plan, context, character_state
        )
        return (
            before, e, decision, effect_plan, after,
            character_state, contract, audit,
        )

    def test_pipeline_stages_are_explicit_and_physical_state_is_not_reduced_by_vn(self):
        before, _, decision, plan, after, state_audit, contract, audit = (
            self.explicit_lab_pipeline()
        )
        self.assertEqual(decision["disposition"], "accept")
        self.assertEqual(plan["state_effects"][1]["type"], "converse")
        self.assertEqual(len(plan["actions"]), 1)
        self.assertEqual(plan["actions"][0]["action_type"], "navigate")
        self.assertEqual(plan["actions"][0]["target_id"], "laboratory")
        self.assertEqual(after["scene_id"], before["scene_id"])
        self.assertFalse(state_audit["physical_movement_applied"])
        self.assertEqual(self.store.state(), before)
        self.assertEqual(contract["effect_plan"], plan)
        self.assertFalse(
            audit["character_state"]["physical_movement_applied"]
        )

    def test_action_executor_dispatches_exact_approved_semantic_target(self):
        _, _, _, plan, _, _, _, _ = self.explicit_lab_pipeline()
        navigation = NavigationStub()
        executor = ActionExecutor(navigation_factory=lambda: navigation)
        proposal = plan["actions"][0]
        result = executor.start(proposal, "action.pipeline-0001")
        self.assertEqual(result["status"], "queued")
        self.assertEqual(
            navigation.starts,
            [("navigate", "laboratory", "action.pipeline-0001")],
        )
        self.assertEqual(result["action_id"], "nav.pipeline.1")

    def test_action_start_result_is_given_to_narrator_without_claiming_arrival(self):
        before, e, _, plan, after, state_audit, contract, _ = (
            self.explicit_lab_pipeline()
        )
        navigation = NavigationStub(status="queued")
        executor = ActionExecutor(navigation_factory=lambda: navigation)
        result = executor.start(plan["actions"][0], "action.pipeline-0001")
        action_results = [{
            "proposal_id": plan["actions"][0]["proposal_id"],
            "request_id": result["request_id"],
            "status": result["status"],
            "action_id": result["action_id"],
            "result": result,
        }]
        data = prompts.packet(e, before, self.rules)
        facts = prompts.narration_facts(
            data, before, contract, state_audit, after, action_results
        )
        prompt = prompts.narration(facts)
        self.assertEqual(facts["action_results"][0]["status"], "queued")
        self.assertIn('"status": "queued"', prompt)
        self.assertIn("arrived", prompt)
        self.assertNotIn("laboratory_step", prompt)

    def test_uncertain_dispatch_is_one_attempt_and_never_blind_retried(self):
        _, _, _, plan, _, _, _, _ = self.explicit_lab_pipeline()
        navigation = NavigationStub(fail=True)
        executor = ActionExecutor(navigation_factory=lambda: navigation)
        result = executor.start(plan["actions"][0], "action.pipeline-0001")
        self.assertEqual(len(navigation.starts), 1)
        self.assertTrue(result["uncertain"])
        self.assertEqual(result["status"], "uncertain")

    def test_review_prompt_does_not_mutate_frozen_action_facts(self):
        before, e, _, _, after, state_audit, contract, _ = (
            self.explicit_lab_pipeline()
        )
        data = prompts.packet(e, before, self.rules)
        facts = prompts.narration_facts(
            data, before, contract, state_audit, after, []
        )
        frozen = copy.deepcopy(facts)
        prompts.review(
            facts,
            {
                "event_id": e["id"],
                "disposition": "accept",
                "text": "test",
            },
        )
        self.assertEqual(facts, frozen)

    def test_ordinary_voices_deny_tools_and_executor_uses_approval_scope(self):
        roleplay = Path(__file__).parents[1] / "roleplay"
        runtime = (roleplay / "runtime.py").read_text()
        external = (roleplay / "external.py").read_text()
        self.assertNotIn("lab=True", runtime)
        self.assertNotIn("lab=True", external)
        self.assertIn("allowed_tools=(tool,)", external)
        self.assertIn("execute_approved", external)
        self.assertIn("service.navigate", external)
        self.assertIn("service.approach", external)


if __name__ == "__main__":
    unittest.main()
