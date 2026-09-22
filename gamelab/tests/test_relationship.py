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
        self.assertGreater(state["stats"]["warmth"], 15)

    def test_first_contact_is_relationship_event_but_lab_meeting_needs_access(self):
        state = self.runtime.state()
        self.assertEqual(state["recent_events"][0]["kind"], "first_meeting")
        with self.assertRaises(RelationshipError):
            self.runtime.event(kind="first_lab_meeting", evidence_note="met in laboratory")
        self.runtime.event(kind="access_granted", evidence_note="fictional laboratory pass granted")
        state = self.runtime.event(kind="first_lab_meeting", evidence_note="Director arrived in laboratory")
        self.assertTrue(state["location"]["first_lab_meeting_occurred"])

    def test_relationship_exists_before_executive_attachment(self):
        other = RelationshipRuntime(Path(self.temp.name) / "other", clock=self.clock)
        state = other.begin(first_impression="First words from Director", duration_minutes=180)
        self.assertIsNone(state["executive_session_id"])
        self.assertEqual(state["relationship_stage"], "professional")

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
        self.assertEqual(migrated["relationship_version"], 2)
        self.assertEqual(migrated["relationship_session_id"], "executive-1")
        self.assertTrue(migrated["location"]["first_lab_meeting_occurred"])

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
        with self.assertRaises(RelationshipError): self.runtime.state()


if __name__ == "__main__": unittest.main()
