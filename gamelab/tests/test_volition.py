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

    def begin_cycle(self, event="Director offered an employment-linked personal request", action="respond"):
        state = self.runtime.cycle_begin(action=action, shared_event=event)
        return state["active_cycle"]["cycle_id"]

    def record_voices(self, cycle_id):
        self.runtime.record_voice(
            cycle_id=cycle_id, side="heart", direction="strengthen",
            intensity="strong", position="Heart wants closeness despite the risk",
            evidence_note="attachment and fear of loss",
        )
        self.runtime.record_voice(
            cycle_id=cycle_id, side="brain", direction="strengthen",
            intensity="strong", position="Head wants to preserve professional independence",
            evidence_note="employment dependency and uncertainty",
        )

    def record_will(
        self, cycle_id, *, behavior="complied", voluntariness="coerced",
        desire="opposed", readiness="closed", alignment="diverged",
        agency="impaired", pressure="overwhelming", stress="overwhelming",
    ):
        return self.runtime.will_appraise(
            cycle_id=cycle_id,
            reported_action="respond to the Director's request",
            desire=desire,
            readiness=readiness,
            intended_choice="refuse",
            predicted_behavior=behavior,
            voluntariness=voluntariness,
            alignment=alignment,
            agency=agency,
            pressure=pressure,
            stress=stress,
            evidence_note="Will predicts behavior from both fresh voices under pressure",
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
            assessment="The audience expects compliance", evidence_note="audience tick 1",
        )
        self.assertEqual(state["recent_chorus"][-1]["pressure_type"], "conformity")
        self.assertEqual(state["recent_decisions"], [])

    def test_direct_decision_is_disabled(self):
        with self.assertRaisesRegex(VolitionError, "direct volition decision is disabled"):
            self.runtime.decide(
                action="request", intended_choice="refuse", behavior="refused",
                voluntariness="free", desire="opposed", readiness="closed",
                alignment="aligned", evidence_note="parent tried to decide directly",
            )

    def test_will_requires_both_fresh_voices(self):
        cycle_id = self.begin_cycle()
        with self.assertRaisesRegex(VolitionError, "fresh Heart and Head"):
            self.record_will(cycle_id)
        self.runtime.record_voice(
            cycle_id=cycle_id, side="heart", direction="strengthen",
            intensity="meaningful", position="Heart position", evidence_note="heart evidence",
        )
        with self.assertRaisesRegex(VolitionError, "fresh Heart and Head"):
            self.record_will(cycle_id)

    def test_commit_uses_will_behavior_not_parent_override(self):
        cycle_id = self.begin_cycle()
        self.record_voices(cycle_id)
        self.record_will(cycle_id)
        state = self.runtime.commit(cycle_id=cycle_id, evidence_note="commit system result")
        decision = state["recent_decisions"][-1]
        self.assertEqual(decision["behavior"], "complied")
        self.assertEqual(decision["voluntariness"], "coerced")
        self.assertEqual(decision["desire"], "opposed")
        self.assertEqual(decision["intention_behavior_alignment"], "diverged")
        self.assertEqual(decision["classification"], "complied_under_duress")
        self.assertEqual(decision["consent_effect"], "no_change_separate_explicit_consent_required")
        self.assertEqual(decision["agency"], "impaired")

    def test_commit_before_will_is_rejected(self):
        cycle_id = self.begin_cycle()
        self.record_voices(cycle_id)
        with self.assertRaisesRegex(VolitionError, "before Will/Ego"):
            self.runtime.commit(cycle_id=cycle_id, evidence_note="too early")

    def test_changed_event_supersedes_old_cycle_and_invalidates_old_reports(self):
        old_id = self.begin_cycle(event="Director offered an unspecified small favor")
        self.record_voices(old_id)
        state = self.runtime.cycle_begin(
            action="respond",
            shared_event="Director now explicitly stated a different concrete condition",
        )
        new_id = state["active_cycle"]["cycle_id"]
        self.assertNotEqual(new_id, old_id)
        with self.assertRaisesRegex(VolitionError, "stale deliberation cycle"):
            self.runtime.record_voice(
                cycle_id=old_id, side="heart", direction="strengthen",
                intensity="strong", position="stale", evidence_note="stale",
            )

    def test_parent_appraisal_is_telemetry_not_decision_authority(self):
        state = self.runtime.appraise(
            action="request", desire="strongly_opposed", readiness="closed",
            pressure="overwhelming", agency="intact", stress="strong",
            evidence_note="parent self-report",
        )
        self.assertEqual(state["current_appraisals"]["request"]["source"], "parent_telemetry")
        self.assertEqual(state["recent_decisions"], [])

    def test_desire_and_current_readiness_can_disagree(self):
        state = self.runtime.appraise(
            action="kiss", desire="strongly_wants", readiness="closed",
            pressure="none", agency="intact", stress="meaningful",
            evidence_note="Yuki wants closeness but the current setting feels wrong",
        )
        appraisal = state["current_appraisals"]["kiss"]
        self.assertEqual(appraisal["desire"], "strongly_wants")
        self.assertEqual(appraisal["readiness"], "closed")

    def test_character_core_cannot_change_inside_persistent_relationship(self):
        changed = CharacterCore().public()
        changed["profile_sha256"] = "0" * 64
        with self.assertRaises(VolitionError):
            self.runtime.begin(
                relationship_session_id="relationship-1",
                deadline_at=11800.0,
                character_core=changed,
            )

    def test_deadline_ends_shift_but_deliberation_can_continue(self):
        self.clock.value = 11800.0
        self.assertEqual(self.runtime.state()["status"], "deadline_reached")
        cycle_id = self.begin_cycle(event="post-shift personal conversation", action="respond")
        self.record_voices(cycle_id)
        self.record_will(
            cycle_id, behavior="refused", voluntariness="free",
            desire="opposed", readiness="guarded", alignment="aligned",
            agency="intact", pressure="none", stress="faint",
        )
        state = self.runtime.commit(cycle_id=cycle_id, evidence_note="post-shift personal decision")
        self.assertEqual(state["recent_decisions"][-1]["behavior"], "refused")


if __name__ == "__main__":
    unittest.main()
