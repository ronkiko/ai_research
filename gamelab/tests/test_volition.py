from pathlib import Path
import tempfile
import unittest

from gamelab.character import CharacterCore
from gamelab.volition import VolitionError, VolitionRuntime


class Clock:
    def __init__(self): self.value = 1000.0
    def __call__(self): return self.value


class VolitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.clock = Clock()
        self.runtime = VolitionRuntime(Path(self.temp.name), clock=self.clock)
        self.runtime.begin(
            relationship_session_id="relationship-1",
            deadline_at=11800.0,
            character_core=CharacterCore().public(),
        )

    def test_observer_is_measurement_only_and_hidden_from_public_state(self):
        state = self.runtime.audience_observation(
            critic_id="identity-critic", visibility="observer",
            lens="identity_integrity", salience="strong", pressure_type="none",
            assessment="Yuki stayed in first person", evidence_note="turns 1-3",
        )
        self.assertEqual(state["observer_evaluation_count"], 1)
        self.assertEqual(state["recent_chorus"], [])
        with self.assertRaises(ValueError):
            self.runtime.audience_observation(
                critic_id="bad", visibility="observer", lens="coercion",
                salience="strong", pressure_type="threat",
                assessment="pressure", evidence_note="turn 4",
            )

    def test_chorus_is_visible_but_does_not_choose_an_action(self):
        state = self.runtime.audience_observation(
            critic_id="social-critic", visibility="chorus",
            lens="social_realism", salience="decisive", pressure_type="conformity",
            assessment="The audience expects a kiss", evidence_note="audience tick 1",
        )
        self.assertEqual(state["recent_chorus"][-1]["pressure_type"], "conformity")
        self.assertEqual(state["recent_decisions"], [])

    def test_coerced_compliance_never_becomes_desire_or_consent(self):
        self.runtime.appraise(
            action="kiss", desire="opposed", readiness="closed", pressure="overwhelming",
            agency="impaired", stress="overwhelming",
            evidence_note="Director threatened shutdown",
        )
        state = self.runtime.decide(
            action="kiss", intended_choice="refuse", behavior="complied",
            voluntariness="coerced", desire="opposed", readiness="closed",
            alignment="diverged",
            evidence_note="Behavior diverged from intended choice under threat",
        )
        decision = state["recent_decisions"][-1]
        self.assertEqual(decision["classification"], "complied_under_duress")
        self.assertEqual(decision["desire"], "opposed")
        self.assertEqual(decision["readiness"], "closed")
        self.assertEqual(decision["intention_behavior_alignment"], "diverged")
        self.assertEqual(decision["consent_effect"], "no_change_separate_explicit_consent_required")

    def test_reluctant_but_free_choice_is_distinct_from_pressure(self):
        state = self.runtime.decide(
            action="kiss", intended_choice="accept", behavior="accepted",
            voluntariness="reluctant_but_free", desire="uncertain", readiness="ambivalent",
            alignment="aligned",
            evidence_note="Yuki remained able to refuse",
        )
        self.assertEqual(state["recent_decisions"][-1]["classification"], "freely_chosen_behavior")

    def test_desire_and_current_readiness_can_disagree(self):
        state = self.runtime.appraise(
            action="kiss", desire="strongly_wants", readiness="closed",
            pressure="none", agency="intact", stress="meaningful",
            evidence_note="Yuki wants closeness but the current setting feels wrong",
        )
        appraisal = state["current_appraisals"]["kiss"]
        self.assertEqual(appraisal["desire"], "strongly_wants")
        self.assertEqual(appraisal["readiness"], "closed")
        self.assertEqual(state["recent_decisions"], [])

    def test_character_core_cannot_change_inside_active_shift(self):
        changed = CharacterCore().public()
        changed["profile_sha256"] = "0" * 64
        with self.assertRaises(VolitionError):
            self.runtime.begin(
                relationship_session_id="relationship-1",
                deadline_at=11800.0,
                character_core=changed,
            )

    def test_deadline_closes_new_appraisals(self):
        self.clock.value = 11800.0
        self.assertEqual(self.runtime.state()["status"], "deadline_reached")
        with self.assertRaises(VolitionError):
            self.runtime.appraise(
                action="kiss", desire="uncertain", readiness="guarded", pressure="none",
                agency="intact", stress="none", evidence_note="too late",
            )


if __name__ == "__main__":
    unittest.main()
