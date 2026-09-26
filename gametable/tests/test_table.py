from __future__ import annotations

import copy
import json
import io
from types import SimpleNamespace
from unittest.mock import Mock
from pathlib import Path
import tempfile
import threading
import unittest

from gameclient.v1.clients.base import HostClientError
from gametable.roleplay.engine import (DISPOSITIONS, InvalidReport, initial_state, load_rules,
                                     plan_effects, reduce_turn, reduce_world,
                                     validate_action_proposal, validate_draft, validate_report)
from gametable.roleplay.external import ActionExecutor, ActionScopeError
from gametable.roleplay.opencode import (
    LEARNING_TOOLS, NAVIGATION_TOOLS, READ_ONLY_LEARNING_TOOLS,
    BackendError, OpenCode,
)
from graphics import LegacyVNGraphics
from gametable.roleplay.runtime import Runtime, public_turn
from gametable.roleplay.store import Store
from gametable.roleplay.server import (Application, EventHub, handler_for,
                                       normalize_turn_body, sse_frame)
from gametable.roleplay.view import available_intent_ids, project_view


def event(event_id="turn-0001", text="Привет, Юки", intent_id="talk"):
    return {"id": event_id, "text": text, "intent_id": intent_id}


def report(role, e, state, disposition="respond"):
    return {"event_id": e["id"], "revision": state["revision"], "role": role,
            "category": "neutral", "impacts": {"mood": 0, "affection": 0, "trust": 0},
            "scores": {d: (0.9 if d == disposition else -0.5) for d in DISPOSITIONS},
            "evidence": [e["text"]], "summary": "Нейтральный разговор"}


class RulesTests(unittest.TestCase):
    def setUp(self):
        self.rules = load_rules()
        self.state = initial_state(self.rules)
        self.event = event()
        self.heart = report("heart", self.event, self.state)
        self.head = report("head", self.event, self.state)

    def run_turn(self, state=None, e=None, h=None, d=None):
        return reduce_turn(state or self.state, e or self.event,
                           h or self.heart, d or self.head, self.rules)

    def test_neutral_turn_does_not_farm_relationship_or_health(self):
        after, contract, audit = self.run_turn()
        for key in ("health", "affection", "trust", "mood"):
            self.assertEqual(after["stats"][key], self.state["stats"][key])
        self.assertEqual(after["stats"]["fatigue"], 11)
        self.assertEqual(contract["decision"]["disposition"], "respond")
        self.assertEqual(after["minutes"], 542)
        self.assertEqual(self.run_turn(), (after, contract, audit))
        self.assertEqual(self.state["revision"], 0)

    def test_reports_reject_stale_spoofed_and_nonfinite_values(self):
        mutations = (("event_id", "other"), ("revision", 3), ("role", "head"))
        for key, value in mutations:
            bad = copy.deepcopy(self.heart); bad[key] = value
            with self.assertRaises(InvalidReport):
                validate_report(bad, self.event, self.state, "heart")
        for value in (float('nan'), float('inf'), True, 3, "1"):
            bad = copy.deepcopy(self.heart); bad["impacts"]["trust"] = value
            with self.assertRaises(InvalidReport):
                self.run_turn(h=bad)
        bad = copy.deepcopy(self.heart); bad["evidence"] = ["Несуществующий факт"]
        with self.assertRaises(InvalidReport): self.run_turn(h=bad)
        bad = copy.deepcopy(self.heart); bad["impacts"]["health"] = -2
        with self.assertRaises(InvalidReport): self.run_turn(h=bad)

    def test_each_voice_has_causal_influence(self):
        base = self.run_turn()[0]
        self.heart["impacts"]["affection"] = 2
        changed = self.run_turn()[0]
        self.assertGreater(changed["stats"]["affection"], base["stats"]["affection"])
        self.head["impacts"]["trust"] = -2
        changed = self.run_turn()[0]
        self.assertLess(changed["stats"]["trust"], base["stats"]["trust"])
        # Construct a close decision and change only one voice, not its counterpart.
        for r in (self.heart, self.head):
            r["scores"] = dict.fromkeys(DISPOSITIONS, -1)
            r["scores"].update(respond=-0.1, clarify=0)
        for role in (self.heart, self.head):
            previous = role["scores"]["clarify"]
            role["scores"]["clarify"] = 1
            self.assertEqual(self.run_turn()[1]["decision"]["disposition"], "clarify")
            role["scores"]["clarify"] = previous
        self.assertEqual(self.run_turn()[1]["decision"]["disposition"], "respond")

    def test_exhaustion_forces_rest_and_blocks_lab(self):
        self.state["stats"]["fatigue"] = 90
        self.event["intent_id"] = "request_lab_work"
        for r in (self.heart, self.head): r["scores"]["accept"] = 1
        after, contract, _ = self.run_turn()
        self.assertEqual(contract["decision"]["disposition"], "decline")
        self.assertEqual([e["type"] for e in contract["effect_plan"]["state_effects"]],
                         ["social", "rest"])
        self.assertEqual(contract["effect_plan"]["actions"], [])
        self.assertEqual(after["scene_id"], "hallway")
        self.assertEqual(after["stats"]["fatigue"], 72)

    def test_rest_sleep_and_no_wall_clock_progress(self):
        self.state["stats"].update(health=60, fatigue=80)
        self.event["intent_id"] = "request_sleep"
        for r in (self.heart, self.head):
            r["scores"] = dict.fromkeys(DISPOSITIONS, -1)
            r["scores"]["accept"] = 1
        after, contract, _ = self.run_turn()
        self.assertEqual(after["stats"]["health"], 75)
        self.assertEqual(after["stats"]["fatigue"], 0)
        self.assertEqual(contract["minutes"], 480)
        self.assertEqual(after["stats"]["affection"], 15)

    def test_talk_cannot_launch_work_even_if_both_prefer_accept(self):
        for r in (self.heart, self.head):
            r["scores"] = dict.fromkeys(DISPOSITIONS, -1)
            r["scores"]["accept"] = 1
        _, contract, _ = self.run_turn()
        self.assertEqual([e["type"] for e in contract["effect_plan"]["state_effects"]],
                         ["social", "converse"])
        self.assertEqual(contract["effect_plan"]["actions"], [])
        self.assertNotEqual(contract["intent_id"], "request_lab_work")

    def test_repeated_praise_has_diminishing_effect_and_caps(self):
        for r in (self.heart, self.head):
            r["category"] = "praise"; r["impacts"]["affection"] = 2
        first, _, _ = self.run_turn()
        e = event("turn-0002")
        h, d = copy.deepcopy(self.heart), copy.deepcopy(self.head)
        for r in (h, d): r.update(event_id=e["id"], revision=first["revision"])
        second, _, _ = self.run_turn(first, e, h, d)
        self.assertLess(second["stats"]["affection"]-first["stats"]["affection"],
                        first["stats"]["affection"]-self.state["stats"]["affection"])
        self.state["stats"]["affection"] = 99
        self.assertEqual(self.run_turn()[0]["stats"]["affection"], 100)

    def test_profile_matters_and_conflict_is_not_averaged_away(self):
        self.heart["impacts"]["affection"] = 1
        original = self.run_turn()[0]["stats"]["affection"]
        self.rules["character"]["traits"]["attachment"] = 0
        self.assertLess(self.run_turn()[0]["stats"]["affection"], original)
        self.heart["scores"] = dict.fromkeys(DISPOSITIONS, 1)
        self.head["scores"] = dict.fromkeys(DISPOSITIONS, -1)
        self.assertEqual(self.run_turn()[1]["conflict"], 2)

    def test_narrator_cannot_change_contract_fields(self):
        contract = self.run_turn()[1]
        with self.assertRaises(InvalidReport):
            validate_draft({"event_id": self.event["id"], "disposition": "accept", "text": "Пойду работать"}, contract)


    def test_lab_request_decline_has_no_work_effect(self):
        self.event["intent_id"] = "request_lab_work"
        for r in (self.heart, self.head):
            r["scores"] = dict.fromkeys(DISPOSITIONS, -1)
            r["scores"]["decline"] = 1
        after, contract, _ = self.run_turn()
        self.assertEqual(contract["decision"], {"disposition": "decline", "tone": "firm"})
        self.assertEqual([e["type"] for e in contract["effect_plan"]["state_effects"]],
                         ["social", "converse"])
        self.assertEqual(contract["effect_plan"]["actions"], [])
        self.assertEqual(after["scene_id"], "hallway")

    def test_fresh_scene_is_hallway(self):
        self.assertEqual(self.state["scene_id"], "hallway")
        self.assertNotIn("location", self.state)

    def test_accepted_lab_request_proposes_navigation_but_does_not_move_state(self):
        self.event["intent_id"] = "request_lab_work"
        for r in (self.heart, self.head):
            r["scores"] = dict.fromkeys(DISPOSITIONS, -1)
            r["scores"]["accept"] = 1
        after, contract, _ = self.run_turn()
        self.assertEqual(contract["decision"]["disposition"], "accept")
        self.assertEqual([e["type"] for e in contract["effect_plan"]["state_effects"]],
                         ["social", "converse"])
        actions = contract["effect_plan"]["actions"]
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["action_type"], "navigate")
        self.assertEqual(actions[0]["target_id"], "laboratory")
        self.assertEqual(actions[0]["source"], "director_request")
        self.assertEqual(contract["minutes"], 2)
        self.assertEqual(after["scene_id"], "hallway")

    def test_clarified_lab_request_does_not_propose_action(self):
        self.event["intent_id"] = "request_lab_work"
        for r in (self.heart, self.head):
            r["scores"] = dict.fromkeys(DISPOSITIONS, -1)
            r["scores"]["clarify"] = 1
        after, contract, _ = self.run_turn()
        self.assertEqual(contract["decision"]["disposition"], "clarify")
        self.assertEqual(after["scene_id"], "hallway")
        self.assertEqual(contract["effect_plan"]["actions"], [])

    def test_physical_observation_routes_return_without_mutating_legacy_scene(self):
        leave = event("turn-0002", "Пойдём обратно в коридор", "request_leave_lab")
        state = copy.deepcopy(self.state)
        h = report("heart", leave, state, "accept")
        d = report("head", leave, state, "accept")
        observation = {
            "world_epoch": "epoch.1", "observed_tick": 42,
            "location_id": "laboratory",
        }
        after, contract, _ = reduce_turn(
            state, leave, h, d, self.rules, observation=observation
        )
        self.assertEqual(after["scene_id"], "hallway")
        action = contract["effect_plan"]["actions"][0]
        self.assertEqual(action["target_id"], "hallway")
        self.assertEqual(action["observation_ref"]["location_id"], "laboratory")

    def test_move_or_lab_work_cannot_be_forged_into_character_state_reducer(self):
        decision = {"disposition": "accept", "tone": "neutral"}
        forged = {
            "state_effects": [
                {"type": "social", "delta": {"mood": 0, "affection": 0, "trust": 0}},
                {"type": "move", "minutes": 2},
            ],
            "actions": [],
            "duration": 2,
        }
        with self.assertRaises(ValueError):
            reduce_world(self.state, self.event, decision, forged, self.rules,
                         "fingerprint", "neutral")

    def test_tone_is_separate_from_disposition(self):
        for r in (self.heart, self.head):
            r["scores"] = dict.fromkeys(DISPOSITIONS, -1)
            r["scores"]["respond"] = 1
        _, initial_contract, _ = self.run_turn()
        warmer = copy.deepcopy(self.state)
        warmer["stats"].update(affection=80, mood=80)
        h, d = copy.deepcopy(self.heart), copy.deepcopy(self.head)
        _, warm_contract, _ = self.run_turn(state=warmer, h=h, d=d)
        self.assertEqual(initial_contract["decision"]["disposition"], "respond")
        self.assertEqual(warm_contract["decision"]["disposition"], "respond")
        self.assertNotEqual(initial_contract["decision"]["tone"], warm_contract["decision"]["tone"])

    def test_director_intent_is_authoritative_input_contract(self):
        bad = copy.deepcopy(self.event)
        bad["intent_id"] = "teleport_to_lab"
        with self.assertRaises(ValueError):
            self.run_turn(e=bad)

class FakeActions:
    def __init__(self, status="queued"):
        self.status = status
        self.started = []
        self.polled = []

    def start(self, proposal, request_id):
        self.started.append((copy.deepcopy(proposal), request_id))
        return {
            "proposal": copy.deepcopy(proposal),
            "request_id": request_id,
            "status": self.status,
            "uncertain": self.status == "uncertain",
            "action_id": "nav.test.1",
            "result": {
                "action_id": "nav.test.1",
                "request_id": request_id,
                "status": self.status,
            },
        }

    def poll(self, action_id):
        self.polled.append(action_id)
        return {
            "action_id": action_id,
            "status": self.status,
            "uncertain": self.status == "uncertain",
            "result": {
                "action_id": action_id,
                "status": self.status,
                "freshness": {"same_epoch": True, "age_ticks": 0},
                "current_observation": {
                    "world_epoch": "epoch.test",
                    "observed_tick": 12,
                    "location_id": "hallway",
                    "physical": {"x": 10.0, "vx": 0.0, "effort": 0.0},
                },
            },
        }


class FakeNavigation:
    def __init__(self):
        self.calls = []
    def navigate(self, target, request_id):
        self.calls.append(("navigate", target, request_id))
        return {"action_id": "nav.1", "request_id": request_id, "status": "queued"}
    def approach(self, target, request_id):
        self.calls.append(("approach", target, request_id))
        return {"action_id": "nav.2", "request_id": request_id, "status": "queued"}
    def action_status(self, action_id):
        return {"action_id": action_id, "status": "arrived",
                "current_observation": {"world_epoch": "e", "observed_tick": 2,
                                        "location_id": "laboratory",
                                        "physical": {"x": 500.0, "vx": 0.0, "effort": 0.0}}}
    def close(self): pass


class FakeBackend:
    model = "test/fake"
    def __init__(self, e, state, reject=False, fail_voice=False, disposition=None):
        self.e, self.state = e, state
        self.calls = []
        self.reject, self.fail_voice = reject, fail_voice
        self.disposition = disposition
        self.barrier = threading.Barrier(2, timeout=3)
        self.lab_calls = 0

    def create(self, title): return "ses_parent"
    def close_sessions(self): pass
    def complete(self, parent, agent, prompt, lab=False, **kwargs):
        self.calls.append((agent, prompt, lab))
        if "MODE: APPRAISAL" in prompt:
            self.barrier.wait()  # Proves both calls were dispatched independently.
            role = agent.removeprefix("yuki-")
            if self.fail_voice and role == "head": raise BackendError("missing head")
            chosen = self.disposition or ("accept" if self.e["intent_id"] == "request_lab_work" else "respond")
            value = report(role, self.e, self.state, chosen)
        elif "MODE: REVIEW" in prompt:
            value = {"event_id": self.e["id"], "ok": not self.reject, "reason": "test"}
        elif lab:
            self.lab_calls += 1
            return {"session_id": "ses_lab", "text": "Недоступно", "tools": []}
        else:
            chosen = self.disposition or ("accept" if self.e["intent_id"] == "request_lab_work" else "respond")
            value = {"event_id": self.e["id"], "disposition": chosen, "text": "Привет, Директор."}
        return {"session_id": "ses_"+agent, "text": json.dumps(value), "tools": []}


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "save.sqlite3"
        self.rules = load_rules()
        self.store = Store(self.path, self.rules)
        self.event = event()
    def tearDown(self):
        self.store.close(); self.temp.cleanup()

    def run_runtime(self, action_status="queued", **kwargs):
        backend = FakeBackend(self.event, self.store.state(), **kwargs)
        actions = FakeActions(action_status)
        runtime = Runtime(self.store, backend, self.rules, actions=actions)
        self.store.begin(self.event)
        runtime.run(self.event)
        return backend, actions, self.store.get(self.event["id"])

    def test_commit_is_persistent_idempotent_and_auditable(self):
        backend, actions, turn = self.run_runtime()
        self.assertEqual(turn["status"], "done", turn.get("error"))
        self.assertEqual(self.store.state()["revision"], 1)
        self.assertFalse(self.store.begin(self.event))
        self.assertEqual(len(self.store.history()), 1)
        self.assertEqual(turn["result"]["assessments"]["heart"]["report"]["role"], "heart")
        self.assertEqual(self.store.state()["memories"][-1]["yuki"]["text"], "Привет, Директор.")
        for agent, prompt, _ in backend.calls[:2]:
            self.assertNotIn('"assessments"', prompt)
        self.store.close(); self.store = Store(self.path, self.rules)
        self.assertEqual(self.store.state()["revision"], 1)

    def test_failed_head_cannot_be_invented_by_parent(self):
        _, _, turn = self.run_runtime(fail_voice=True)
        self.assertEqual(turn["status"], "failed")
        self.assertEqual(self.store.state()["revision"], 0)
        self.assertNotIn("reply", public_turn(turn))

    def test_semantic_rejection_has_fixed_fallback_and_no_leaked_draft(self):
        backend, actions, turn = self.run_runtime(reject=True)
        self.assertTrue(turn["result"]["reply"]["fallback"])
        self.assertEqual(turn["result"]["reply"]["text"], "")
        self.assertEqual(len(turn["result"]["draft_attempts"]), 2)
        self.assertNotIn("Привет, Директор.", json.dumps(public_turn(turn), ensure_ascii=False))

    def test_inflight_drafts_are_not_public(self):
        self.store.begin(self.event)
        self.store.progress(self.event["id"], "Проверка", {"draft": "unreviewed"})
        self.assertNotIn("unreviewed", json.dumps(public_turn(self.store.get(self.event["id"]))))

    def test_accepted_lab_request_starts_navigation_without_claiming_arrival(self):
        self.event["intent_id"] = "request_lab_work"
        backend, actions, turn = self.run_runtime()
        self.assertEqual(turn["status"], "done", turn.get("error"))
        self.assertEqual(turn["result"]["contract"]["decision"]["disposition"], "accept")
        self.assertEqual(turn["result"]["after"]["scene_id"], "hallway")
        self.assertEqual(len(actions.started), 1)
        proposal, request_id = actions.started[0]
        self.assertEqual(proposal["action_type"], "navigate")
        self.assertEqual(proposal["target_id"], "laboratory")
        self.assertTrue(request_id.startswith("action.proposal."))
        self.assertEqual(turn["result"]["actions"]["results"][0]["status"], "queued")
        self.assertNotEqual(turn["result"]["actions"]["results"][0]["status"], "arrived")
        self.assertEqual(backend.lab_calls, 0)

    def test_declined_lab_request_never_starts_body_action(self):
        self.event["intent_id"] = "request_lab_work"
        backend, actions, turn = self.run_runtime(disposition="decline")
        self.assertEqual(turn["status"], "done", turn.get("error"))
        self.assertEqual(turn["result"]["contract"]["decision"]["disposition"], "decline")
        self.assertEqual(turn["result"]["contract"]["effect_plan"]["actions"], [])
        self.assertEqual(actions.started, [])
        self.assertEqual(backend.lab_calls, 0)

    def test_heart_and_head_receive_the_same_frozen_packet(self):
        backend, actions, turn = self.run_runtime()
        self.assertEqual(turn["status"], "done")
        appraisal_prompts = [prompt for _, prompt, _ in backend.calls if "MODE: APPRAISAL" in prompt]
        self.assertEqual(len(appraisal_prompts), 2)
        packets = [json.loads(prompt.split("Ниже данные сцены:\n", 1)[1]) for prompt in appraisal_prompts]
        self.assertEqual(packets[0], packets[1])
        self.assertEqual(packets[0]["event"]["intent_id"], "talk")

    def test_crash_marks_pending_failed_without_replay(self):
        self.store.begin(self.event)
        self.store.progress(self.event["id"], "Лаборатория", {"external_effect": "unknown"})
        self.store.close(); self.store = Store(self.path, self.rules)
        self.assertEqual(self.store.get(self.event["id"])["status"], "failed")
        self.assertEqual(self.store.state()["revision"], 0)
        self.assertFalse(self.store.begin(self.event))

    def test_one_event_id_cannot_hide_a_different_message(self):
        self.store.begin(self.event)
        with self.assertRaises(ValueError): self.store.begin(event(text="other"))

    def test_busy_and_revision_conflicts_are_rejected(self):
        self.store.begin(self.event)
        with self.assertRaises(ValueError): self.store.begin(event("turn-0002"))
        bad = self.store.state(); bad["revision"] = 5
        with self.assertRaises(ValueError): self.store.finish(self.event["id"], bad, bad, {})


class CharacterActionTests(unittest.TestCase):
    def test_self_initiated_navigation_is_validated_and_server_scoped(self):
        proposal = {
            "proposal_id": "proposal.self.demo",
            "source": "self_initiated",
            "action_type": "navigate",
            "target_id": "laboratory",
            "rationale": "Хочу продолжить исследование в лаборатории.",
            "observation_ref": {
                "source": "world", "world_epoch": "epoch.1",
                "observed_tick": 10, "location_id": "hallway",
            },
            "scope": {"capability": "navigate", "target_id": "laboratory"},
        }
        validated = validate_action_proposal(proposal, allowed_source="self_initiated")
        nav = FakeNavigation()
        executor = ActionExecutor(navigation_factory=lambda: nav)
        result = executor.start(validated, "action.self.demo")
        self.assertEqual(result["status"], "queued")
        self.assertEqual(nav.calls, [("navigate", "laboratory", "action.self.demo")])

        forged = copy.deepcopy(proposal)
        forged["scope"]["target_id"] = "training/flat_run"
        with self.assertRaises((ValueError, ActionScopeError)):
            executor.start(forged, "action.self.forged")
        self.assertEqual(len(nav.calls), 1)

    def test_action_outbox_persists_independently_from_turn_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            rules = load_rules()
            store = Store(Path(directory) / "save.sqlite3", rules)
            proposal = {
                "proposal_id": "proposal.turn-persist",
                "source": "director_request",
                "action_type": "navigate",
                "target_id": "laboratory",
                "rationale": "accepted request",
                "observation_ref": {
                    "source": "legacy_vn_hint", "world_epoch": None,
                    "observed_tick": None, "location_id": "hallway",
                },
                "scope": {"capability": "navigate", "target_id": "laboratory"},
            }
            record = store.reserve_action("turn-persist", proposal)
            self.assertTrue(record["created"])
            store.record_action_result(proposal["proposal_id"], {
                "status": "queued", "uncertain": False, "action_id": "nav.persist",
                "result": {"action_id": "nav.persist", "status": "queued"},
            })
            persisted = store.action(proposal["proposal_id"])
            self.assertEqual(persisted["status"], "queued")
            self.assertEqual(persisted["action_id"], "nav.persist")
            store.close()

    def test_ordinary_voice_is_deny_all_but_navigation_scope_can_be_explicit(self):
        backend = OpenCode("http://localhost", "/table", "openai/gpt-5.6-luna")
        backend.request = Mock(return_value={"id": "ses"})
        backend.create("voice", parent="parent")
        ordinary = backend.request.call_args.args[2]["permission"]
        self.assertEqual(ordinary, [{"permission": "*", "pattern": "*", "action": "deny"}])
        backend.create("nav", parent="parent", allowed_tools=NAVIGATION_TOOLS)
        scoped = backend.request.call_args.args[2]["permission"]
        self.assertEqual(scoped[0], {"permission": "*", "pattern": "*", "action": "deny"})
        self.assertEqual({x["permission"] for x in scoped[1:]}, set(NAVIGATION_TOOLS))
        self.assertNotIn("gamelab_v1_training_start", {x["permission"] for x in scoped[1:]})


class TransportTests(unittest.TestCase):
    def test_project_defaults_to_luna(self):
        config = json.loads((Path(__file__).parents[1] / "opencode.json").read_text())
        self.assertEqual(config["model"], "openai/gpt-5.6-luna")
        backend = OpenCode("http://localhost", "/table")
        backend.request = Mock(return_value=config)
        self.assertEqual(backend.select_model(), {"providerID": "openai", "modelID": "gpt-5.6-luna"})
        backend.request.assert_called_once_with("GET", "/config")

    def test_missing_model_does_not_select_an_arbitrary_provider(self):
        backend = OpenCode("http://localhost", "/table")
        backend.request = Mock(return_value={})
        with self.assertRaises(BackendError): backend.select_model()
        backend.request.assert_called_once_with("GET", "/config")

    def test_explicit_override_is_shared_by_all_voices(self):
        backend = OpenCode("http://localhost", "/table", "test/selected")
        backend.request = Mock(return_value={"model": "openai/gpt-5.6-luna"})
        self.assertEqual(backend.select_model(), {"providerID": "test", "modelID": "selected"})
        calls = []
        def request(method, path, body=None, **_):
            calls.append((path, body))
            if path == "/session": return {"id": "ses_child"}
            return {"info": {"providerID": "test", "modelID": "selected"},
                    "parts": [{"type": "text", "text": "{}"}]}
        backend.request = request
        for agent in ("yuki", "yuki-heart", "yuki-head"):
            backend.complete("ses_parent", agent, "test")
        for path, body in calls:
            if path == "/session":
                self.assertEqual(body["permission"], [{"permission": "*", "pattern": "*", "action": "deny"}])
                self.assertEqual(body["parentID"], "ses_parent")
            else:
                self.assertEqual(body["model"], {"providerID": "test", "modelID": "selected"})

    def test_provider_cannot_silently_return_another_model(self):
        backend = OpenCode("http://localhost", "/table", "openai/gpt-5.6-luna")
        backend.request = Mock(side_effect=[{"id": "ses_child"},
            {"info": {"providerID": "other", "modelID": "other"}, "parts": [{"type": "text", "text": "{}"}]}])
        with self.assertRaises(BackendError): backend.complete("ses_parent", "yuki", "test")

    def test_learning_session_has_only_a_scoped_allowlist(self):
        backend = OpenCode("http://localhost", "/table", "openai/gpt-5.6-luna")
        backend.request = Mock(return_value={"id": "ses_lab"})
        backend.create("Lab", parent="ses_parent", lab=True)
        body = backend.request.call_args.args[2]
        rules = body["permission"]
        self.assertEqual(rules[0], {"permission": "*", "pattern": "*", "action": "deny"})
        self.assertEqual({r["permission"] for r in rules[1:]}, set(LEARNING_TOOLS))
        self.assertNotIn("task", {r["permission"] for r in rules})


    def test_read_only_learning_scope_cannot_gain_write_tools(self):
        backend = OpenCode("http://localhost", "/table", "openai/gpt-5.6-luna")
        backend.request = Mock(return_value={"id": "ses_readonly"})
        backend.create("Safe lab", parent="ses_parent", lab_tools=READ_ONLY_LEARNING_TOOLS)
        body = backend.request.call_args.args[2]
        rules = body["permission"]
        self.assertEqual(rules[0], {"permission": "*", "pattern": "*", "action": "deny"})
        self.assertEqual({r["permission"] for r in rules[1:]}, set(READ_ONLY_LEARNING_TOOLS))
        self.assertTrue(set(READ_ONLY_LEARNING_TOOLS) < set(LEARNING_TOOLS))
        self.assertFalse(any(name.endswith(("_start", "_cancel", "_set", "_move"))
                             for name in READ_ONLY_LEARNING_TOOLS))

class WebBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.rules = load_rules()
        self.store = Store(Path(self.temp.name) / "test.sqlite3", self.rules)
        self.app = Application(
            self.store, Mock(), SimpleNamespace(model="openai/gpt-5.6-luna"),
            self.rules, graphics=LegacyVNGraphics(),
        )
        self.handler_class = handler_for(self.app)

    def tearDown(self):
        self.store.close(); self.temp.cleanup()

    def handler(self, path, body=None, token=None, host="127.0.0.1:17880"):
        h = object.__new__(self.handler_class)
        h.path = path
        h.server = SimpleNamespace(server_port=17880)
        raw = json.dumps(body).encode() if body is not None else b""
        h.rfile = io.BytesIO(raw)
        h.headers = {"Host": host, "Content-Length": str(len(raw)), "X-GameTable-Token": token or ""}
        h.send = Mock()
        return h

    def test_sse_journal_reconnects_after_last_event_without_replaying_turn(self):
        hub = EventHub()
        first = hub.publish("turn.started", {"event_id": "turn-0001", "revision": 0})
        second = hub.publish("turn.stage", {"event_id": "turn-0001", "revision": 0,
                                            "stage": "Проверка"})
        self.assertEqual([item["id"] for item in hub.since(0)], [first["id"], second["id"]])
        self.assertEqual(hub.since(second["id"]), [])
        third = hub.publish("state.changed", {"event_id": "turn-0001", "revision": 1})
        self.assertEqual(hub.since(second["id"]), [third])
        frame = sse_frame(third).decode()
        self.assertIn("event: state.changed", frame)
        self.assertIn('"revision":1', frame)
        self.app.runtime.submit.assert_not_called()

    def test_transient_graphics_host_failure_returns_503(self):
        class FailingGraphics:
            def snapshot(self, _state):
                raise HostClientError("temporary snapshot timeout")
        self.app.graphics = FailingGraphics()
        h = self.handler("/api/state")
        h.do_GET()
        self.assertEqual(h.send.call_args.args[0], 503)
        self.assertIn(
            "temporary snapshot timeout",
            h.send.call_args.args[1]["error"],
        )

    def test_graphics_snapshot_and_dialogue_stream_are_stable(self):
        first = self.app.snapshot()
        self.assertEqual(first["graphics"]["frame"]["zone_id"], "hallway")
        self.assertEqual(len(first["graphics"]["terrain"]["cells"]), 1001)
        e = event("turn-0099", text="Привет")
        self.store.begin(e)
        running = self.app.snapshot()
        self.assertEqual(running["dialogue"][-1]["speaker_id"], "director")
        self.assertEqual(running["dialogue"][-1]["message_id"],
                         "dialogue.turn-0099.director")
        self.assertEqual(running["dialogue"][-1]["text"], "Привет")
        again = self.app.snapshot()
        self.assertEqual([x["message_id"] for x in again["dialogue"]],
                         [x["message_id"] for x in running["dialogue"]])

    def test_frame_stream_is_local_only_and_separate_from_turn_events(self):
        h = self.handler("/api/frames?token=forged", host="attacker.example:17880")
        h.do_GET()
        self.assertEqual(h.send.call_args.args[0], 403)
        self.app.runtime.submit.assert_not_called()
        self.assertIsNot(self.app.frames, self.app.events)

    def test_sse_endpoint_is_local_only_and_never_uses_token_query(self):
        h = self.handler("/api/events?token=forged", host="attacker.example:17880")
        h.do_GET()
        self.assertEqual(h.send.call_args.args[0], 403)
        self.app.runtime.submit.assert_not_called()

    def test_cross_origin_cannot_submit_or_read_token(self):
        h = self.handler("/api/turn", event())
        h.do_POST(); self.assertEqual(h.send.call_args.args[0], 403)
        h = self.handler("/api/state", host="attacker.example:17880")
        h.do_GET(); self.assertEqual(h.send.call_args.args[0], 403)
        self.app.runtime.submit.assert_not_called()

    def test_unvalidated_drafts_cannot_be_read_through_audit_endpoint(self):
        e = event(); self.store.begin(e)
        self.store.progress(e["id"], "Checking", {"draft": "SECRET DRAFT"})
        h = self.handler("/api/audit/" + e["id"])
        h.do_GET(); self.assertEqual(h.send.call_args.args[0], 404)
        h = self.handler("/api/state")
        h.do_GET()
        payload = h.send.call_args.args[1]
        self.assertNotIn("SECRET DRAFT", json.dumps(payload))
        self.assertNotIn("draft_attempts", json.dumps(payload))

    def test_hallway_viewstate_is_server_driven_and_does_not_mutate_state(self):
        before = copy.deepcopy(self.store.state())
        view = project_view(before, self.rules)
        self.assertNotIn("scene", view)
        self.assertEqual(view["presentation_mode"], "vn_dialogue")
        self.assertIsNone(view["frame_ref"])
        self.assertEqual([a["intent_id"] for a in view["affordances"]],
                         ["talk", "request_lab_work", "request_rest", "request_sleep"])
        self.assertNotIn("request_leave_lab", [a["intent_id"] for a in view["affordances"]])
        view["stats"]["trust"] = 999
        view["affordances"].append({"intent_id": "forged"})
        self.assertEqual(self.store.state(), before)

    def test_laboratory_viewstate_exposes_leave_intent_without_world_presentation(self):
        state = copy.deepcopy(self.store.state())
        state["scene_id"] = "laboratory.workstation"
        view = project_view(state, self.rules)
        ids = [a["intent_id"] for a in view["affordances"]]
        self.assertIn("request_leave_lab", ids)
        self.assertNotIn("scene", view)
        self.assertNotIn("background", json.dumps(view))
        self.assertNotIn("pose", json.dumps(view))

    def test_snapshot_exposes_viewstate_not_raw_authoritative_state(self):
        snapshot = self.app.snapshot()
        self.assertIn("view", snapshot)
        self.assertNotIn("state", snapshot)
        self.assertNotIn("scene", snapshot["view"])
        self.assertIn("graphics", snapshot)
        self.assertEqual(snapshot["graphics"]["frame"]["zone_id"], "hallway")
        self.assertFalse(snapshot["graphics"]["frame"]["freshness"]["authoritative"])
        self.assertEqual(snapshot["graphics"]["frame"]["freshness"]["source"],
                         "legacy_vn_compat")
        self.assertEqual(snapshot["view"]["frame_ref"]["frame_id"],
                         snapshot["graphics"]["frame"]["frame_id"])
        self.assertIn("dialogue", snapshot)
        dumped = json.dumps(snapshot, ensure_ascii=False)
        self.assertNotIn("rules_hash", dumped)
        self.assertNotIn("recent_events", dumped)
        self.assertNotIn("memories", dumped)

    def test_presentation_labels_do_not_change_world_reducer(self):
        variant = copy.deepcopy(self.rules)
        variant["scenes"]["hallway"]["presentation"]["label"] = "ДРУГАЯ ПОДПИСЬ"
        base = self.store.state()
        e = event()
        h = report("heart", e, base)
        d = report("head", e, base)
        original = reduce_turn(base, e, h, d, self.rules)
        changed = reduce_turn(base, e, h, d, variant)
        self.assertEqual(original, changed)

    def test_unavailable_intent_is_rejected_for_current_scene(self):
        allowed = available_intent_ids(self.store.state(), self.rules)
        with self.assertRaises(ValueError):
            normalize_turn_body(
                {"id": "turn-0003", "text": "Выйдем", "intent_id": "request_leave_lab"},
                self.rules,
                allowed,
            )
        h = self.handler(
            "/api/turn",
            {"id": "turn-0003", "text": "Выйдем", "intent_id": "request_leave_lab"},
            token=self.app.token,
        )
        h.do_POST()
        self.assertEqual(h.send.call_args.args[0], 400)
        self.app.runtime.submit.assert_not_called()

    def test_legacy_activity_request_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_turn_body(
                {"id": "turn-0001", "text": "Проверь стенд", "activity": "lab"},
                self.rules,
            )
        native = {"id": "turn-0002", "text": "Привет", "intent_id": "talk"}
        self.assertEqual(normalize_turn_body(native, self.rules), native)
        self.assertNotIn("ui_activity_compat", self.rules)

    def test_browser_shell_is_module_driven_csp_safe_and_has_no_polling(self):
        web = Path(__file__).parents[1] / "web"
        index = (web / "index.html").read_text()
        scripts = "\n".join(path.read_text() for path in sorted((web / "js").glob("*.js")))
        renderer = (web / "js" / "scene-renderer.js").read_text()
        controls = (web / "js" / "controls.js").read_text()
        self.assertNotIn("<style", index.lower())
        self.assertNotIn(" style=", index.lower())
        self.assertIn('type="module" src="/js/shell.js"', index)
        self.assertNotIn("setInterval(", scripts)
        self.assertNotIn("request_lab_work", renderer)
        self.assertNotIn("laboratory.workstation", renderer)
        self.assertNotIn("view.scene", renderer)
        frame_renderer = (web / "js" / "frame-renderer.js").read_text()
        self.assertIn("frame.zone_id", frame_renderer)
        self.assertNotIn("portal.", frame_renderer)
        self.assertIn("items.map", controls)
        self.assertFalse((web / "app.js").exists())
        self.assertFalse((web / "style.css").exists())

    def test_csp_does_not_allow_inline_script_or_style(self):
        source = Path(__file__).parents[1] / "roleplay" / "server.py"
        text = source.read_text()
        self.assertIn("script-src 'self'", text)
        self.assertIn("style-src 'self'", text)
        self.assertNotIn("'unsafe-inline'", text)
        self.assertIn("connect-src 'self'", text)

    def test_cutover_has_no_legacy_activity_contract_or_temp_plan(self):
        root = Path(__file__).parents[1]
        server = (root / "roleplay" / "server.py").read_text()
        rules = json.loads((root / "roleplay" / "rules.json").read_text())
        self.assertNotIn("ui_activity_compat", rules)
        self.assertNotIn('{"id", "text", "activity"}', server)
        self.assertFalse((root / "refactor-plan-v2").exists())

    def test_ci_watches_embodied_navigation_and_learning_surfaces(self):
        workflow = (Path(__file__).parents[2] / ".github" / "workflows" / "gametable.yml").read_text()
        self.assertIn('"organism/**"', workflow)
        self.assertIn('"gameserver/v1/world/**"', workflow)
        self.assertIn('"gameclient/v1/host/**"', workflow)
        self.assertIn('"graphics/**"', workflow)
        self.assertIn('"world/navigation.py"', workflow)
        self.assertNotIn('"gamelab/mcp.py"', workflow)

    def test_active_opencode_config_has_only_navigation_and_learning(self):
        root = Path(__file__).parents[1]
        config = json.loads((root / "opencode.json").read_text())
        self.assertEqual(set(config["mcp"]), {"navigation_v1", "learning_v1"})
        start = (root / "op" / "start.sh").read_text()
        self.assertNotIn("gamelab", start.lower())
        self.assertIn("gameserver/v1/op/embodied.sh", start)
        self.assertIn("organism/op/organism.sh", start)

    def test_no_set_stats_or_arbitrary_activity_endpoint(self):
        h = self.handler("/api/set_stats", {"health": 0}, token=self.app.token)
        h.do_POST(); self.assertEqual(h.send.call_args.args[0], 404)
        h = self.handler("/api/turn", {"id": "turn-0001", "text": "Привет, Юки", "activity": "injury"}, token=self.app.token)
        h.do_POST(); self.assertEqual(h.send.call_args.args[0], 400)
        self.app.runtime.submit.assert_not_called()


if __name__ == '__main__': unittest.main()
