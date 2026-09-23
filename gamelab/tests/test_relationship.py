from pathlib import Path
import json
import tempfile
import unittest

from gamelab.relationship import RelationshipError, RelationshipRuntime


class Clock:
    def __init__(self): self.value = 1000.0
    def __call__(self): return self.value


class RelationshipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.clock = Clock(); self.runtime = RelationshipRuntime(Path(self.temp.name), clock=self.clock)
        self.runtime.begin(first_impression="Director greeted Yuki", duration_minutes=180)
        self.runtime.attach_executive("executive-1")

    def test_persists_and_keeps_same_executive_session(self):
        self.runtime.event(kind="director_concern", evidence_note="Director asked whether Yuki needs a break")
        resumed = RelationshipRuntime(Path(self.temp.name), clock=self.clock)
        state = resumed.state()
        self.assertEqual(state["executive_session_id"], "executive-1")
        self.assertEqual(state["recent_events"][-1]["kind"], "director_concern")
        self.assertNotIn("stats", state)
        self.assertNotIn("relationship_stage", state)

    def test_runtime_classifies_first_and_repeated_contact_by_proximity(self):
        state = self.runtime.state()
        self.assertEqual(state["recent_events"][0]["kind"], "contact")
        self.assertEqual(state["contacts"]["contact_count"], 1)
        self.clock.value += 10
        self.runtime.contact(proximity="close", evidence_note="Director is nearby and speaking")
        self.clock.value += 10
        state = self.runtime.contact(proximity="physical", evidence_note="Director and Yuki shook hands")
        self.assertEqual(state["contacts"]["contact_count"], 3)
        self.assertEqual(state["contacts"]["close_contact_count"], 1)
        self.assertEqual(state["contacts"]["physical_contact_count"], 1)
        self.assertEqual(state["contacts"]["closest_contact_reached"], "physical")
        self.assertEqual(state["contacts"]["first_close_contact_at"], 1010.0)
        self.assertEqual(state["consent"]["affectionate_touch"]["director"], "unknown")

    def test_contact_is_independent_of_laboratory_access(self):
        state = self.runtime.contact(proximity="close", evidence_note="Director is beside Yuki outside the lab")
        self.assertFalse(state["location"]["access_granted"])
        self.assertEqual(state["contacts"]["last_contact_proximity"], "close")

    def test_relationship_exists_before_executive_attachment(self):
        other = RelationshipRuntime(Path(self.temp.name) / "other", clock=self.clock)
        state = other.begin(first_impression="First words from Director", duration_minutes=180)
        self.assertIsNone(state["executive_session_id"])
        self.assertEqual(state["status"], "active")
        self.assertNotIn("relationship_stage", state)

    def test_v1_state_migrates_physical_meeting_without_changing_meaning(self):
        old_root = Path(self.temp.name) / "old"
        old_root.mkdir()
        old_state = dict(self.runtime._state)
        old_state.update(version=1, relationship_session_id=None)
        old_state.pop("relationship_session_id")
        old_state["location"] = {
            "state": "laboratory", "access_granted": True, "first_meeting_occurred": True,
        }
        (old_root / "relationship-current.json").write_text(json.dumps(old_state), encoding="utf-8")
        migrated = RelationshipRuntime(old_root, clock=self.clock).state()
        self.assertEqual(migrated["relationship_version"], 4)
        self.assertEqual(migrated["relationship_session_id"], "executive-1")
        self.assertGreaterEqual(migrated["contacts"]["close_contact_count"], 1)
        self.assertNotIn("stats", migrated)

    def test_consent_is_per_action_and_per_actor(self):
        state = self.runtime.consent(action="embrace", actor="brain", state="accepted", evidence_note="Yuki agreed")
        self.assertEqual(state["consent"]["embrace"]["brain"], "accepted")
        self.assertEqual(state["consent"]["embrace"]["director"], "unknown")
        self.assertEqual(state["consent"]["kiss"]["brain"], "unknown")

    def test_journal_keeps_record_type_separate_from_story_event(self):
        self.runtime.event(kind="access_granted", evidence_note="fictional pass granted")
        relationship_id = self.runtime.state()["relationship_session_id"]
        journal = Path(self.temp.name, f"{relationship_id}.relationship.jsonl")
        records = [json.loads(line) for line in journal.read_text().splitlines()]
        self.assertEqual(records[-1]["kind"], "event")
        self.assertEqual(records[-1]["relationship_kind"], "access_granted")

    def test_unknown_combined_event_is_rejected_without_mutation(self):
        before = self.runtime.state()
        with self.assertRaises(ValueError):
            self.runtime.event(kind="praise_and_welcome", evidence_note="combined label")
        self.assertEqual(self.runtime.state()["recent_events"], before["recent_events"])

    def test_hiring_closes_employment_goal_without_implying_physical_consent(self):
        summary = self.runtime.finish(employment_decision="hired", director_statement="You are hired")
        self.assertEqual(summary["employment"]["status"], "permanent_employee")
        self.assertEqual(summary["consent"]["private_intimacy"]["director"], "unknown")
        self.assertEqual(self.runtime.state()["status"], "finished")
        with self.assertRaises(RelationshipError):
            self.runtime.event(kind="director_attention", evidence_note="too late")

    def test_elapsed_time_does_not_create_trust_or_a_relationship_stage(self):
        before = self.runtime.state()
        self.clock.value += 60 * 60
        after = self.runtime.state()
        self.assertEqual(after["recent_events"], before["recent_events"])
        self.assertNotIn("stats", after)
        self.assertNotIn("relationship_stage", after)
        self.assertNotIn("yandere_tension", after)

    def test_deadline_closes_writes_but_keeps_state_readable(self):
        deadline = self.runtime._state["deadline_at"]
        self.clock.value = deadline
        state = self.runtime.state()
        self.assertEqual(state["status"], "deadline_reached")
        self.assertEqual(state["time_remaining_seconds"], 0.0)
        with self.assertRaises(RelationshipError):
            self.runtime.action(kind="offer_support", note="after deadline")

    def test_new_shift_can_begin_after_expired_shift(self):
        previous_id = self.runtime.state()["relationship_session_id"]
        self.clock.value = self.runtime._state["deadline_at"]
        state = self.runtime.begin(first_impression="Director starts a later shift")
        self.assertEqual(state["status"], "active")
        self.assertNotEqual(state["relationship_session_id"], previous_id)

    def test_v3_scores_are_discarded_while_factual_history_survives(self):
        root = Path(self.temp.name) / "v3"
        root.mkdir()
        old_state = dict(self.runtime._state)
        old_state["version"] = 3
        old_state["stats"] = {"trust": 99, "warmth": 100}
        (root / "relationship-current.json").write_text(json.dumps(old_state), encoding="utf-8")
        migrated = RelationshipRuntime(root, clock=self.clock).state()
        self.assertEqual(migrated["relationship_version"], 4)
        self.assertNotIn("stats", migrated)
        self.assertEqual(migrated["recent_events"], old_state["events"][-10:])


if __name__ == "__main__": unittest.main()
