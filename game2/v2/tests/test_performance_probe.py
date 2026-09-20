from __future__ import annotations

import unittest

from game2.v2.performance_probe import (
    run_engine_probe,
    run_model_probe,
    run_unpaced_profile,
)


class PerformanceProbeTests(unittest.TestCase):
    def test_model_probe_runs_real_model_without_engine(self):
        result = run_model_probe(
            decisions=4, seed=1, threads=1, json_output=True, progress_every=0
        )
        self.assertEqual(result["decisions"], 4)
        self.assertEqual(result["rollout_records"], 4)
        self.assertGreaterEqual(float(result["decision_seconds"]), 0.0)
        self.assertGreaterEqual(float(result["ppo_seconds"]), 0.0)

    def test_engine_probe_runs_requested_ticks_without_model(self):
        result = run_engine_probe(ticks=8, json_output=True)
        self.assertEqual(result["ticks"], 8)
        self.assertGreaterEqual(float(result["engine_seconds"]), 0.0)

    def test_unpaced_profile_accounts_for_major_stages(self):
        result = run_unpaced_profile(
            ticks=4, seed=1, threads=1, json_output=True, progress_every=0
        )
        self.assertEqual(result["ticks"], 4)
        self.assertEqual(result["result"], "timeout")
        for name in (
            "vision_before_seconds",
            "bookkeeping_before_seconds",
            "inference_seconds",
            "input_seconds",
            "engine_seconds",
            "vision_after_seconds",
            "bookkeeping_after_seconds",
            "ppo_seconds",
            "checkpoint_seconds",
        ):
            self.assertIn(name, result)
            self.assertGreaterEqual(float(result[name]), 0.0)


if __name__ == "__main__":
    unittest.main()
