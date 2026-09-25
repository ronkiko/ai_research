from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from gamelab.executive import BrainExecutive, ExecutiveError


class Clock:
    def __init__(self, value: float = 1000.0):
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class BrainExecutiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = Clock()
        self.executive = BrainExecutive(Path(self.temp.name), clock=self.clock)

    def begin(self, **kwargs):
        return self.executive.begin(
            objective="Put player at x=987",
            acceptance_criteria="machine-observed stable success",
            duration_minutes=kwargs.pop("duration_minutes", 180),
            plateau_minutes=kwargs.pop("plateau_minutes", 15),
            **kwargs,
        )

    def test_budget_is_capped_at_three_hours(self):
        with self.assertRaisesRegex(ValueError, r"\[1,180\]"):
            self.executive.begin(objective="x", acceptance_criteria="y", duration_minutes=181)
        state = self.begin()
        self.assertEqual(state["phase"], "orientation")
        self.assertLessEqual(state["time_remaining_seconds"], 10800)

    def test_failed_strategy_retry_without_evidence_is_visible(self):
        self.begin()
        args = dict(
            name="manual pulses", hypothesis="small pulses can settle",
            expected_signal="error decreases", budget="5 minutes",
            stop_condition="no improvement", next_if_positive="verify",
            next_if_negative="train controller",
        )
        self.executive.strategy_begin(**args)
        self.executive.strategy_end(outcome="failed", evidence_note="overshoots repeatedly")
        state = self.executive.strategy_begin(**args)
        self.assertIn("STRATEGY_RELAPSE", state["alerts"])
        self.assertEqual(state["metrics"]["strategy_relapses"], 1)
        self.assertIn("manual pulses", state["tabu_without_new_evidence"])

    def test_new_evidence_allows_retry_without_relapse(self):
        self.begin()
        args = dict(
            name="train", hypothesis="more training helps", expected_signal="lower error",
            budget="20 episodes", stop_condition="plateau", next_if_positive="verify",
            next_if_negative="change reward",
        )
        self.executive.strategy_begin(**args)
        self.executive.strategy_end(outcome="failed", evidence_note="no signal")
        state = self.executive.strategy_begin(**args, new_evidence="network latency fixed")
        self.assertNotIn("STRATEGY_RELAPSE", state["alerts"])

    def test_information_director_signal_is_valid(self):
        self.begin()
        state = self.executive.director_signal(
            kind="information",
            text="Director reported a GameLab system error.",
        )
        self.assertEqual(state["director_signals"][-1]["kind"], "information")

    def test_plateau_and_help_are_surface_signals_not_actions(self):
        self.begin(plateau_minutes=1)
        self.executive.director_signal(kind="offer_help", text="Ask me for one hint")
        self.executive.strategy_begin(
            name="train", hypothesis="learn", expected_signal="lower error",
            budget="10 episodes", stop_condition="no change", next_if_positive="verify",
            next_if_negative="ask a question",
        )
        self.clock.advance(61)
        state = self.executive.state()
        self.assertIn("PLATEAU", state["alerts"])
        self.assertIn("HELP_AVAILABLE", state["alerts"])
        self.assertEqual(state["current_strategy"]["name"], "train")
        help_id = state["available_help"][0]["signal_id"]
        state = self.executive.question(text="What should I inspect?", reason="plateau", help_signal_id=help_id)
        self.assertNotIn("HELP_AVAILABLE", state["alerts"])
        self.assertEqual(state["metrics"]["help_opportunities_used"], 1)

    def test_new_strategy_gets_its_own_plateau_window(self):
        self.begin(plateau_minutes=1)
        self.clock.advance(120)
        state = self.executive.strategy_begin(
            name="fresh idea", hypothesis="new information changes approach",
            expected_signal="new result", budget="1 minute",
            stop_condition="no improvement", next_if_positive="verify",
            next_if_negative="stop", new_evidence="new observation",
        )
        self.assertNotIn("PLATEAU", state["alerts"])
        self.clock.advance(61)
        self.assertIn("PLATEAU", self.executive.state()["alerts"])

    def test_machine_results_keep_best_separate_from_current(self):
        self.begin()
        self.executive.operation_started("run", {"experiment_id":"r1", "status":"starting"})
        self.executive.operation_status("run", {
            "experiment_id":"r1", "status":"active", "target_x":987,
            "x":986.5, "error":0.5, "goal_revision":1,
        })
        self.executive.operation_status("run", {
            "experiment_id":"r1", "status":"timeout", "target_x":987,
            "x":766, "error":221, "goal_revision":1,
        })
        state = self.executive.state()
        self.assertEqual(state["best_result"]["metric"], 0.5)
        self.assertEqual(state["current_result"]["metric"], 221.0)

    def test_training_budget_is_accounted_from_machine_status(self):
        self.begin()
        self.executive.operation_started("training", {
            "experiment_id":"t1", "status":"starting", "episodes_requested":50,
            "episodes_completed":0,
        })
        self.executive.operation_status("training", {
            "experiment_id":"t1", "status":"cancelled", "episodes_requested":50,
            "episodes_completed":11, "recent_episodes":[],
        })
        state = self.executive.state()
        self.assertEqual(state["metrics"]["training_requested_episodes"], 50)
        self.assertEqual(state["metrics"]["training_completed_episodes"], 11)
        self.assertAlmostEqual(state["metrics"]["training_budget_completion"], 11/50)
        self.assertIn("BUDGET_AT_RISK", state["alerts"])

    def test_verified_result_is_separate(self):
        self.begin()
        self.executive.operation_started("verify", {
            "experiment_id":"v1", "status":"starting", "runs_requested":1,
        })
        self.executive.operation_status("verify", {
            "experiment_id":"v1", "status":"passed", "target_x":987,
            "runs_completed":1, "results":[{"run":1,"pass":True,"status":"reached","x":987.2,"error":-0.2}],
        })
        state = self.executive.state()
        self.assertTrue(state["best_verified_result"]["verified"])
        self.assertEqual(state["metrics"]["verified_successes"], 1)

    def test_finish_produces_factual_summary_and_closes_active_strategy(self):
        self.begin()
        self.executive.strategy_begin(
            name="train", hypothesis="learn", expected_signal="lower error",
            budget="10 episodes", stop_condition="plateau", next_if_positive="verify",
            next_if_negative="change",
        )
        summary = self.executive.finish(conclusion="criterion not yet met")
        self.assertEqual(summary["strategies"][0]["outcome"], "inconclusive")
        with self.assertRaises(ExecutiveError):
            self.executive.state()


if __name__ == "__main__":
    unittest.main()