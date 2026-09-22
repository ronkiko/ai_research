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
        self.runtime.begin(executive_session_id="executive-1", deadline_at=11800.0)

    def test_persists_and_keeps_same_executive_session(self):
        self.runtime.event(kind="director_concern", evidence_note="Director asked whether Yuki needs a break")
        resumed = RelationshipRuntime(Path(self.temp.name), clock=self.clock)
        state = resumed.state()
        self.assertEqual(state["executive_session_id"], "executive-1")
        self.assertGreater(state["stats"]["warmth"], 15)

    def test_first_meeting_needs_narrative_access(self):
        with self.assertRaises(RelationshipError):
            self.runtime.event(kind="first_meeting", evidence_note="met")
        self.runtime.event(kind="access_granted", evidence_note="fictional laboratory pass granted")
        state = self.runtime.event(kind="first_meeting", evidence_note="Director arrived in laboratory")
        self.assertTrue(state["location"]["first_meeting_occurred"])

    def test_consent_is_per_action_and_per_actor(self):
        state = self.runtime.consent(action="embrace", actor="brain", state="accepted", evidence_note="Yuki agreed")
        self.assertEqual(state["consent"]["embrace"]["brain"], "accepted")
        self.assertEqual(state["consent"]["embrace"]["director"], "unknown")
        self.assertEqual(state["consent"]["kiss"]["brain"], "unknown")

    def test_journal_keeps_record_type_separate_from_story_event(self):
        self.runtime.event(kind="access_granted", evidence_note="fictional pass granted")
        journal = Path(self.temp.name, "executive-1.relationship.jsonl")
        records = [json.loads(line) for line in journal.read_text().splitlines()]
        self.assertEqual(records[-1]["kind"], "event")
        self.assertEqual(records[-1]["relationship_kind"], "access_granted")

    def test_hiring_closes_employment_goal_without_implying_physical_consent(self):
        summary = self.runtime.finish(employment_decision="hired", director_statement="You are hired")
        self.assertEqual(summary["employment"]["status"], "permanent_employee")
        self.assertEqual(summary["consent"]["private_intimacy"]["director"], "unknown")
        with self.assertRaises(RelationshipError): self.runtime.state()


if __name__ == "__main__": unittest.main()
