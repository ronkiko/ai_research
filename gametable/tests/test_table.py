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

from gametable.roleplay.engine import (DISPOSITIONS, InvalidReport, initial_state, load_rules,
                                     plan_effects, reduce_turn, reduce_world,
                                     validate_draft, validate_report)
from gametable.roleplay.opencode import LAB_TOOLS, BackendError, OpenCode
from gametable.roleplay.runtime import Runtime, public_turn
from gametable.roleplay.store import Store
from gametable.roleplay.server import Application, handler_for, normalize_turn_body
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
        self.assertEqual([e["type"] for e in contract["effect_plan"]["world_effects"]],
                         ["social", "rest"])
        self.assertEqual(contract["effect_plan"]["external_effects"], [])
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
        self.assertEqual([e["type"] for e in contract["effect_plan"]["world_effects"]],
                         ["social", "converse"])
        self.assertEqual(contract["effect_plan"]["external_effects"], [])
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
        self.assertEqual([e["type"] for e in contract["effect_plan"]["world_effects"]],
                         ["social", "converse"])
        self.assertEqual(contract["effect_plan"]["external_effects"], [])
        self.assertEqual(after["scene_id"], "hallway")

    def test_fresh_scene_is_hallway(self):
        self.assertEqual(self.state["scene_id"], "hallway")
        self.assertNotIn("location", self.state)

    def test_accepted_lab_request_moves_then_works(self):
        self.event["intent_id"] = "request_lab_work"
        for r in (self.heart, self.head):
            r["scores"] = dict.fromkeys(DISPOSITIONS, -1)
            r["scores"]["accept"] = 1
        after, contract, _ = self.run_turn()
        self.assertEqual(contract["decision"]["disposition"], "accept")
        self.assertEqual([e["type"] for e in contract["effect_plan"]["world_effects"]],
                         ["social", "move", "lab_work"])
        self.assertEqual(contract["effect_plan"]["external_effects"],
                         [{"type": "laboratory_step"}])
        self.assertEqual(contract["minutes"], 22)
        self.assertEqual(after["scene_id"], "laboratory.workstation")

    def test_clarified_lab_request_does_not_move(self):
        self.event["intent_id"] = "request_lab_work"
        for r in (self.heart, self.head):
            r["scores"] = dict.fromkeys(DISPOSITIONS, -1)
            r["scores"]["clarify"] = 1
        after, contract, _ = self.run_turn()
        self.assertEqual(contract["decision"]["disposition"], "clarify")
        self.assertEqual(after["scene_id"], "hallway")
        self.assertFalse(any(e["type"] == "move"
                             for e in contract["effect_plan"]["world_effects"]))
        self.assertEqual(contract["effect_plan"]["external_effects"], [])

    def test_laboratory_has_normal_return_transition(self):
        self.event["intent_id"] = "request_lab_work"
        for r in (self.heart, self.head):
            r["scores"] = dict.fromkeys(DISPOSITIONS, -1)
            r["scores"]["accept"] = 1
        in_lab, _, _ = self.run_turn()

        leave = event("turn-0002", "Пойдём обратно в коридор", "request_leave_lab")
        h = report("heart", leave, in_lab, "accept")
        d = report("head", leave, in_lab, "accept")
        back, contract, _ = self.run_turn(in_lab, leave, h, d)
        self.assertEqual(back["scene_id"], "hallway")
        self.assertEqual([e["type"] for e in contract["effect_plan"]["world_effects"]],
                         ["social", "move"])
        self.assertEqual(contract["minutes"], 2)

    def test_invalid_transition_and_work_without_workstation_are_rejected(self):
        leave = event("turn-0002", "Выйдем", "request_leave_lab")
        decision = {"disposition": "accept", "tone": "neutral"}
        with self.assertRaises(ValueError):
            plan_effects(self.state, leave, decision,
                         {"mood": 0, "affection": 0, "trust": 0}, self.rules)

        forged = {
            "world_effects": [
                {"type": "social", "delta": {"mood": 0, "affection": 0, "trust": 0}},
                {"type": "lab_work", "minutes": 20},
            ],
            "external_effects": [{"type": "laboratory_step"}],
            "duration": 20,
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
    def complete(self, parent, agent, prompt, lab=False):
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

    def run_runtime(self, **kwargs):
        backend = FakeBackend(self.event, self.store.state(), **kwargs)
        runtime = Runtime(self.store, backend, self.rules)
        self.store.begin(self.event)
        runtime.run(self.event)
        return backend, self.store.get(self.event["id"])

    def test_commit_is_persistent_idempotent_and_auditable(self):
        backend, turn = self.run_runtime()
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
        _, turn = self.run_runtime(fail_voice=True)
        self.assertEqual(turn["status"], "failed")
        self.assertEqual(self.store.state()["revision"], 0)
        self.assertNotIn("reply", public_turn(turn))

    def test_semantic_rejection_has_fixed_fallback_and_no_leaked_draft(self):
        backend, turn = self.run_runtime(reject=True)
        self.assertTrue(turn["result"]["reply"]["fallback"])
        self.assertEqual(turn["result"]["reply"]["text"], "")
        self.assertEqual(len(turn["result"]["draft_attempts"]), 2)
        self.assertNotIn("Привет, Директор.", json.dumps(public_turn(turn), ensure_ascii=False))

    def test_inflight_drafts_are_not_public(self):
        self.store.begin(self.event)
        self.store.progress(self.event["id"], "Проверка", {"draft": "unreviewed"})
        self.assertNotIn("unreviewed", json.dumps(public_turn(self.store.get(self.event["id"]))))

    def test_lab_requires_explicit_mode_and_cannot_write_social_state(self):
        self.event["intent_id"] = "request_lab_work"
        backend, turn = self.run_runtime()
        self.assertEqual(backend.lab_calls, 1)
        self.assertEqual(turn["result"]["contract"]["decision"]["disposition"], "accept")
        self.assertEqual(turn["result"]["after"]["scene_id"], "laboratory.workstation")
        self.assertEqual(turn["result"]["contract"]["effect_plan"]["external_effects"],
                         [{"type": "laboratory_step"}])
        for name in LAB_TOOLS:
            self.assertFalse(any(s in name for s in ("relationship", "volition", "duality", "executive")))

    def test_declined_lab_request_never_calls_mcp(self):
        self.event["intent_id"] = "request_lab_work"
        backend, turn = self.run_runtime(disposition="decline")
        self.assertEqual(turn["status"], "done", turn.get("error"))
        self.assertEqual(turn["result"]["contract"]["decision"]["disposition"], "decline")
        self.assertEqual(turn["result"]["contract"]["effect_plan"]["external_effects"], [])
        self.assertEqual(backend.lab_calls, 0)

    def test_heart_and_head_receive_the_same_frozen_packet(self):
        backend, turn = self.run_runtime()
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

    def test_lab_session_has_only_a_scoped_allowlist(self):
        backend = OpenCode("http://localhost", "/table", "openai/gpt-5.6-luna")
        backend.request = Mock(return_value={"id": "ses_lab"})
        backend.create("Lab", parent="ses_parent", lab=True)
        body = backend.request.call_args.args[2]
        rules = body["permission"]
        self.assertEqual(rules[0], {"permission": "*", "pattern": "*", "action": "deny"})
        self.assertEqual({r["permission"] for r in rules[1:]}, set(LAB_TOOLS))
        self.assertNotIn("task", {r["permission"] for r in rules})


class WebBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.rules = load_rules()
        self.store = Store(Path(self.temp.name) / "test.sqlite3", self.rules)
        self.app = Application(self.store, Mock(), SimpleNamespace(model="openai/gpt-5.6-luna"), self.rules)
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
        self.assertEqual(view["scene"]["id"], "hallway")
        self.assertEqual(view["scene"]["label"], "КОРИДОР · HALLWAY")
        self.assertEqual([a["intent_id"] for a in view["affordances"]],
                         ["talk", "request_lab_work", "request_rest", "request_sleep"])
        self.assertNotIn("request_leave_lab", [a["intent_id"] for a in view["affordances"]])
        view["stats"]["trust"] = 999
        view["scene"]["props"].append("forged")
        self.assertEqual(self.store.state(), before)

    def test_laboratory_viewstate_exposes_leave_intent(self):
        state = copy.deepcopy(self.store.state())
        state["scene_id"] = "laboratory.workstation"
        view = project_view(state, self.rules)
        ids = [a["intent_id"] for a in view["affordances"]]
        self.assertIn("request_leave_lab", ids)
        self.assertEqual(view["scene"]["css_class"], "laboratory")
        self.assertEqual(view["scene"]["character"]["pose"], "seated_working")

    def test_snapshot_exposes_viewstate_not_raw_authoritative_state(self):
        snapshot = self.app.snapshot()
        self.assertIn("view", snapshot)
        self.assertNotIn("state", snapshot)
        self.assertEqual(snapshot["view"]["scene"]["id"], "hallway")
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

    def test_http_compat_activity_is_normalized_to_director_intent(self):
        body = {"id": "turn-0001", "text": "Проверь стенд", "activity": "lab"}
        self.assertEqual(
            normalize_turn_body(body, self.rules),
            {"id": "turn-0001", "text": "Проверь стенд", "intent_id": "request_lab_work"},
        )
        native = {"id": "turn-0002", "text": "Привет", "intent_id": "talk"}
        self.assertEqual(normalize_turn_body(native, self.rules), native)

    def test_no_set_stats_or_arbitrary_activity_endpoint(self):
        h = self.handler("/api/set_stats", {"health": 0}, token=self.app.token)
        h.do_POST(); self.assertEqual(h.send.call_args.args[0], 404)
        h = self.handler("/api/turn", {"id": "turn-0001", "text": "Привет, Юки", "activity": "injury"}, token=self.app.token)
        h.do_POST(); self.assertEqual(h.send.call_args.args[0], 400)
        self.app.runtime.submit.assert_not_called()


if __name__ == '__main__': unittest.main()
