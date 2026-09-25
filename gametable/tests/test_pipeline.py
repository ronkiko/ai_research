from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import unittest

from gametable.roleplay import prompts
from gametable.roleplay.engine import (apply_effect_plan, build_contract, build_effect_plan,
                                      calculation_audit, decide_turn, load_rules)
from gametable.roleplay.external import ExternalExecutor
from gametable.roleplay.opencode import BackendError
from gametable.roleplay.store import Store


def event(intent_id="talk"):
    return {"id": "pipeline-0001", "text": "Проверь лабораторный стенд", "intent_id": intent_id}


def report(role, e, state, disposition="respond"):
    return {"event_id": e["id"], "revision": state["revision"], "role": role,
            "category": "research", "impacts": {"mood": 0, "affection": 0, "trust": 0},
            "scores": {name: (1 if name == disposition else -1)
                       for name in ("respond", "accept", "decline", "clarify")},
            "evidence": [e["text"]], "summary": "test"}


class ExternalBackend:
    model = "test/model"

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    def complete(self, parent, agent, prompt, lab=False):
        self.calls += 1
        if self.fail:
            raise BackendError("transport lost")
        return {"session_id": "ses_lab", "text": "health ok",
                "tools": [{"tool": "gamelab_v1_health",
                           "state": {"status": "completed", "output": "healthy"}}]}


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
        effect_plan = build_effect_plan(before, e, decision, context, self.rules)
        after, world = apply_effect_plan(before, e, decision, effect_plan, context, self.rules)
        contract = build_contract(e, after, decision, effect_plan, context, self.rules)
        audit = calculation_audit(decision, effect_plan, context, world)
        return before, e, decision, effect_plan, after, world, contract, audit

    def test_pipeline_stages_are_explicit_and_world_state_is_still_provisional(self):
        before, _, decision, plan, after, _, contract, audit = self.explicit_lab_pipeline()
        self.assertEqual(decision["disposition"], "accept")
        self.assertEqual(plan["external_effects"], [{"type": "laboratory_step"}])
        self.assertEqual(after["scene_id"], "laboratory.workstation")
        self.assertEqual(self.store.state(), before)
        self.assertEqual(contract["effect_plan"], plan)
        self.assertEqual(audit["world"]["scene_after"], "laboratory.workstation")

    def test_external_executor_does_nothing_without_external_effects(self):
        backend = ExternalBackend()
        executor = ExternalExecutor(backend)
        self.assertEqual(executor.execute_all("parent", {}, []), [])
        self.assertEqual(backend.calls, 0)

    def test_external_executor_observation_is_given_to_narrator(self):
        before, e, _, plan, after, world, contract, _ = self.explicit_lab_pipeline()
        data = prompts.packet(e, before, self.rules)
        backend = ExternalBackend()
        results = ExternalExecutor(backend).execute_all("parent", data, plan["external_effects"])
        facts = prompts.narration_facts(data, before, contract, world, after, results)
        prompt = prompts.narration(facts)
        self.assertEqual(backend.calls, 1)
        self.assertEqual(facts["before"]["scene_id"], "hallway")
        self.assertEqual(facts["after"]["scene_id"], "laboratory.workstation")
        self.assertIn("gamelab_v1_health", prompt)
        self.assertIn('"status": "observed"', prompt)

    def test_uncertain_external_result_is_not_retried(self):
        before, e, _, plan, _, _, _, _ = self.explicit_lab_pipeline()
        backend = ExternalBackend(fail=True)
        results = ExternalExecutor(backend).execute_all(
            "parent", prompts.packet(e, before, self.rules), plan["external_effects"])
        self.assertEqual(backend.calls, 1)
        self.assertTrue(results[0]["uncertain"])
        self.assertEqual(results[0]["status"], "uncertain")

    def test_review_prompt_does_not_mutate_frozen_facts(self):
        before, e, _, _, after, world, contract, _ = self.explicit_lab_pipeline()
        data = prompts.packet(e, before, self.rules)
        facts = prompts.narration_facts(data, before, contract, world, after, [])
        frozen = copy.deepcopy(facts)
        prompts.review(facts, {"event_id": e["id"], "disposition": "accept", "text": "test"})
        self.assertEqual(facts, frozen)

    def test_runtime_source_has_no_mcp_enabled_call(self):
        roleplay = Path(__file__).parents[1] / "roleplay"
        runtime = (roleplay / "runtime.py").read_text()
        external = (roleplay / "external.py").read_text()
        self.assertNotIn("lab=True", runtime)
        self.assertIn("lab=True", external)


if __name__ == "__main__":
    unittest.main()
