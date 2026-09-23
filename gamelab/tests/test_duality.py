from pathlib import Path
import json
import tempfile
import unittest

from gamelab.duality import DualityError, DualityRuntime


class Clock:
    def __init__(self): self.value = 1000.0
    def __call__(self): return self.value


class DualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.clock = Clock()
        self.runtime = DualityRuntime(Path(self.temp.name), clock=self.clock, randint=lambda low, high: high)
        self.runtime.begin(executive_session_id="executive-1", deadline_at=11800.0)

    def appraise(self, side, intensity="decisive"):
        return self.runtime.appraise(
            side=side, direction="strengthen", intensity=intensity,
            position=f"{side} position", evidence_note=f"{side} evidence",
        )

    def test_exact_confidence_is_never_exposed_by_public_state(self):
        for _ in range(3): self.appraise("heart")
        self.appraise("heart", "faint")
        state = self.runtime.state()
        self.assertEqual(state["confidence_telemetry"]["heart"]["level"], "3/4")
        self.assertNotIn("confidence", json.dumps(state).replace("confidence_telemetry", ""))
        self.assertNotIn("99", json.dumps(state))

    def test_ninety_nine_is_blurred_to_three_quarters(self):
        self.runtime._state["confidence"]["heart"] = 99
        telemetry = self.runtime.state()["confidence_telemetry"]["heart"]
        self.assertEqual(telemetry, {"level": "3/4", "all_in_available": False})

    def test_all_in_requires_private_one_hundred_and_forbids_compromise(self):
        self.appraise("heart"); self.appraise("brain")
        self.runtime._state["confidence"].update(heart=99, brain=100)
        with self.assertRaises(DualityError):
            self.runtime.conflict_begin(question="love or exam", stakes="internship", all_in_by="heart")
        self.runtime._state["confidence"]["heart"] = 100
        state = self.runtime.conflict_begin(question="love or exam", stakes="internship", all_in_by="heart")
        self.assertNotIn("private_heart_confidence", state["active_conflict"])
        with self.assertRaises(DualityError):
            self.runtime.resolve(resolution="compromise", decision="delay", rationale="avoid risk")
        state = self.runtime.resolve(resolution="brain", decision="continue exam", rationale="brain prevailed")
        self.assertEqual(state["confidence_telemetry"]["heart"]["level"], "0/4")

    def test_internal_resolution_and_external_outcome_are_separate(self):
        self.appraise("heart"); self.appraise("brain")
        self.runtime.conflict_begin(question="love or exam", stakes="internship")
        self.runtime.resolve(resolution="heart", decision="confess", rationale="heart prevailed")
        state = self.runtime.outcome(outcome="lost", evidence_note="Director did not reciprocate")
        self.assertIsNone(state["active_conflict"])
        self.assertEqual(state["completed_conflicts"], 1)

    def test_absolute_deadline_ends_shift_but_inner_conflict_can_continue(self):
        self.appraise("heart"); self.appraise("brain")
        self.runtime.conflict_begin(question="love or exam", stakes="internship")
        self.clock.value = 11800.0
        state = self.runtime.state()
        self.assertEqual(state["status"], "deadline_finished")
        self.assertIsNotNone(state["active_conflict"])
        state = self.runtime.resolve(
            resolution="heart",
            decision="say the final words honestly",
            rationale="the work shift ended but the personal decision did not disappear",
        )
        self.assertEqual(state["active_conflict"]["resolution"], "heart")


if __name__ == "__main__": unittest.main()
